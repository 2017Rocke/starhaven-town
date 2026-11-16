"""Bounded SSE readers. Partial output is progress, never a completed result."""
import json


class StreamIncomplete(RuntimeError):
    pass


def read_stream(response, protocol, cancel, progress=None, max_bytes=2_000_000):
    total = 0
    fields = []
    text_parts, thinking_chars = [], 0
    usage, stop, started, ended, finished = None, None, False, False, False
    blocks, closed_blocks = {}, set()
    if protocol not in ('anthropic', 'openai'):
        raise ValueError('unsupported_stream_protocol')

    def emit():
        if callable(progress):
            progress(dict(phase="receiving", text_chars=sum(map(len, text_parts))))

    def consume(data):
        nonlocal usage, stop, started, ended, finished, thinking_chars
        if data == "[DONE]":
            if protocol != "openai" or not finished:
                raise StreamIncomplete("模型流式回复提前结束，已保留任务进度，请继续未完成步骤。")
            ended = True
            return
        try:
            item = json.loads(data)
        except (ValueError, TypeError):
            raise StreamIncomplete("模型流式回复格式不完整，已保留任务进度。") from None
        if not isinstance(item, dict) or ended:
            raise StreamIncomplete("模型流式回复顺序无效，结果未被采用。")
        if protocol == "anthropic":
            kind = item.get("type")
            if kind == "error":
                raise StreamIncomplete("模型服务在流式生成期间报错，已保留任务进度。")
            if kind == "ping":
                return
            if kind == "message_start":
                message = item.get("message", {})
                if started or not isinstance(message, dict):
                    raise StreamIncomplete("模型流式消息开头无效。")
                started = True
                raw = message.get("usage")
                usage = dict(raw) if isinstance(raw, dict) else None
            elif not started:
                raise StreamIncomplete("模型流式回复缺少消息开头。")
            elif kind == "content_block_start":
                index, block = item.get("index"), item.get("content_block", {})
                if finished or type(index) is not int or index < 0 or index in blocks or len(blocks) >= 64 or not isinstance(block, dict):
                    raise StreamIncomplete("模型流式内容块无效。")
                if block.get('type') not in ('text', 'thinking', 'redacted_thinking'):
                    raise StreamIncomplete("当前分步工作不执行模型返回的工具调用或其他内容块。")
                blocks[index] = block.get("type")
                if block.get("type") == "text":
                    value = block.get("text", "")
                    if not isinstance(value, str): raise StreamIncomplete("模型流式文字无效。")
                    text_parts.append(value)
                elif block.get("type") == "thinking":
                    thinking_chars += len(block.get("thinking", "")) if isinstance(block.get("thinking", ""), str) else 0
            elif kind == "content_block_delta":
                delta, index = item.get("delta", {}), item.get("index")
                if finished or type(index) is not int or index not in blocks or index in closed_blocks or not isinstance(delta, dict):
                    raise StreamIncomplete("模型流式增量缺少对应内容块。")
                if delta.get("type") == "text_delta":
                    if blocks[index] != "text" or not isinstance(delta.get("text"), str):
                        raise StreamIncomplete("模型流式文字无效。")
                    text_parts.append(delta["text"])
                elif delta.get("type") == "thinking_delta":
                    if blocks[index] != 'thinking' or not isinstance(delta.get("thinking"), str): raise StreamIncomplete("模型流式推理计数无效。")
                    thinking_chars += len(delta["thinking"])
                elif delta.get('type') != 'signature_delta' or blocks[index] != 'thinking':
                    raise StreamIncomplete("模型流式增量类型无效。")
            elif kind == 'content_block_stop':
                index = item.get('index')
                if type(index) is not int or index not in blocks or index in closed_blocks:
                    raise StreamIncomplete("模型流式内容块结束顺序无效。")
                closed_blocks.add(index)
            elif kind == "message_delta":
                if set(blocks) != closed_blocks:
                    raise StreamIncomplete("模型流式回复尚有未完成的内容块。")
                delta = item.get("delta", {})
                if not isinstance(delta, dict): raise StreamIncomplete("模型流式停止状态无效。")
                if delta.get("stop_reason") is not None:
                    stop = delta["stop_reason"]
                    finished = True
                patch = item.get("usage")
                if isinstance(patch, dict) and isinstance(usage, dict):
                    usage.update(patch)
            elif kind == "message_stop":
                if not finished: raise StreamIncomplete("模型流式回复缺少完成状态。")
                ended = True
            else:
                raise StreamIncomplete("模型流式事件类型无效。")
        else:
            if "error" in item:
                raise StreamIncomplete("模型服务在流式生成期间报错，已保留任务进度。")
            started = True
            if isinstance(item.get("usage"), dict): usage = item["usage"]
            choices = item.get("choices", [])
            if not isinstance(choices, list) or len(choices) > 1: raise StreamIncomplete("模型流式选项无效。")
            for choice in choices:
                if not isinstance(choice, dict) or type(choice.get('index', 0)) is not int or choice.get("index", 0) != 0:
                    raise StreamIncomplete("模型流式回复包含不支持的多个结果。")
                delta = choice.get("delta", {})
                if not isinstance(delta, dict): raise StreamIncomplete("模型流式增量无效。")
                content = delta.get("content")
                if content is not None:
                    if finished or not isinstance(content, str): raise StreamIncomplete("模型流式文字无效。")
                    text_parts.append(content)
                reasoning = delta.get("reasoning_content")
                if isinstance(reasoning, str): thinking_chars += len(reasoning)
                if delta.get("tool_calls") or delta.get("function_call"):
                    raise StreamIncomplete("当前分步工作不执行模型返回的工具调用。")
                if choice.get("finish_reason") is not None:
                    stop, finished = choice["finish_reason"], True
        emit()

    while True:
        if cancel.is_set(): raise RuntimeError("已取消这次回复。")
        line = response.readline(262145)
        if cancel.is_set(): raise RuntimeError("已取消这次回复。")
        if not line: break
        total += len(line)
        if total > max_bytes or len(line) > 262144:
            raise StreamIncomplete("模型流式回复过大，请缩小本步任务。")
        try:
            value = line.decode("utf-8").rstrip("\r\n")
        except UnicodeError:
            raise StreamIncomplete("模型流式回复编码无效。") from None
        if not value:
            if fields:
                consume("\n".join(fields)); fields = []
            if ended: break
        elif value.startswith("data:"):
            fields.append(value[5:].lstrip(" "))
    if fields and not ended: consume("\n".join(fields))
    if not started or not ended or not finished:
        raise StreamIncomplete("模型流式连接在结果完成前中断，已保存已完成步骤，请继续当前步骤。")
    final_text = "".join(text_parts)
    if protocol == "anthropic":
        result = dict(content=[dict(type="text", text=final_text)], stop_reason=stop, usage=usage)
    else:
        result = dict(choices=[dict(message=dict(content=final_text), finish_reason=stop)], usage=usage)
    return result, dict(thinking_chars=thinking_chars, text_chars=len(final_text))
