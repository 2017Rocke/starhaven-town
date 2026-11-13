"use strict";

// Only stepped shapes on an integer grid, in the established town palette.
(() => {
  const trees = [[940,-410,2],[1040,-454,1],[1130,-430,0],[1230,-474,1],[1448,-464,2],[1550,-456,0],[1700,-430,2],[1810,-386,1],[1050,-272,2],[1150,-190,0],[1500,-210,1],[1630,-230,2],[1740,-166,0],[970,-140,1],[1860,-240,0]];
  const rect = (x,y,w,h,color) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${color}"/>`;
  const path = (d,color) => `<path d="${d}" fill="${color}"/>`;
  function tree(x,y,size) {
    const unit = size + 2;
    const colors = size === 1 ? ["#6d8b59","#88a46c","#a0b77d","#b7c98d"] : ["#708952","#8fa66a","#a4b67c","#c1cf92"];
    return `<g data-forest-tree="${size}" transform="translate(${x} ${y}) scale(${unit})">` + rect(-10,15,22,3,"#91a674") + rect(-2,-4,4,21,"#927c54") + rect(1,0,2,16,"#7b6c49") + path("M-12-7h-3v-8h3v-5h5v-4H4v3h5v4h5v10h-3v6H3v3h-9v-3h-6Z",colors[0]) + path("M-12-14h3v-5h5v-3H3v3h5v4h4v6H7v5H-2v-2h-7v-3h-3Z",colors[1]) + path("M-8-15h4v-4H3v3h5v4H3v3H-4v-3h-4Z",colors[2]) + rect(-3,-19,4,2,colors[3]) + rect(-10,-9,2,3,colors[2]) + rect(7,-5,3,2,colors[2]) + "</g>";
  }
  function bench(x,y) {
    return `<g data-forest-prop="bench" transform="translate(${x} ${y})">`+rect(0,0,54,8,"#b69a6e")+rect(0,10,54,8,"#ccb58b")+rect(-3,21,60,7,"#b69a6e")+rect(5,28,5,13,"#7c7958")+rect(45,28,5,13,"#7c7958")+rect(0,0,54,2,"#deca9f")+"</g>";
  }
  function lamp(x,y) {
    return `<g data-forest-prop="lamp" transform="translate(${x} ${y})">`+rect(-3,-48,6,50,"#7d886b")+rect(-7,0,14,4,"#8a9273")+rect(-11,-66,22,5,"#718267")+rect(-8,-61,16,16,"#c2b582")+rect(-5,-58,10,10,"#f4dca0")+rect(-5,-71,10,5,"#829174")+"</g>";
  }
  function scene(options = {}) {
    let s = path("M870-468h90v-36h330v-26h310v22h250v44h96v260h-56v112h-232v30h-410v-24H940v-90h-70Z","#cfdfad");
    s += path("M920-444h140v-34h200v-20h300v18h230v34h116v220h-56v100h-244v20h-350v-20H986v-100h-66Z","#d3e2b4");
    // A narrow avenue through the 46px gap between existing northern plots.
    s += rect(1367,-156,26,383,"#c9c69e") + rect(1371,-156,18,383,"#e7ddbb");
    // The northern street provides this bend and its two pedestrian entries.
    // Keep the original connector only for maps without the road renderer.
    if (!options.roadNetwork) {
      s += rect(1367,227,26,66,"#c9c69e") + rect(1371,227,18,62,"#e7ddbb");
      s += rect(1367,267,66,26,"#c9c69e") + rect(1371,271,58,18,"#e7ddbb");
      s += rect(1407,280,26,50,"#c9c69e") + rect(1411,280,18,50,"#e7ddbb");
    }
    // Park loop and three short collection spurs, kept clear of tree trunks.
    const tracks = [[1138,-408,524,18],[1138,-400,18,266],[1644,-400,18,266],[1138,-152,524,18],[1312,-280,16,136],[1228,-288,188,16]];
    for (const [x,y,w,h] of tracks) s += rect(x,y,w,h,"#cabb92") + rect(x+3,y+3,w-6,h-6,"#dfd1a9");
    for (const [x,y] of [[1238,-280],[1320,-280],[1402,-280]]) s += rect(x-19,y-14,38,28,"#dfd1a9")+rect(x-13,y+10,10,2,"#cbbb91");
    s += rect(1358,-170,44,28,"#e7ddbb");
    // The former street lamp now sits in the park, clear of the northern home.
    s += bench(1510,-122) + bench(1184,-364) + lamp(1346,-114) + lamp(1418,-190) + lamp(1678,-130);
    s += `<g data-forest-prop="sign" transform="translate(1434 -108)">`+rect(0,-22,6,58,"#927c54")+rect(74,-22,6,58,"#927c54")+rect(-6,-30,92,35,"#9e875f")+rect(-3,-27,86,29,"#dcc69a")+rect(5,-20,68,3,"#efe0bb")+`</g>`;
    for (const [x,y,size] of trees) s += tree(x,y,size);
    // Small understory details leave broad readable negative space.
    for (const [x,y] of [[1010,-360],[1790,-302],[1210,-118],[1540,-346],[1560,-348],[1474,-382],[1088,-168]]) s += `<g data-forest-prop="flowers">`+rect(x,y,4,12,"#91a573")+rect(x-4,y-2,12,6,"#d8bd87")+rect(x,y-4,4,10,"#e9d49b")+"</g>";
    return s;
  }
  function cargo(amount) {
    let s = "";
    for (let i=0;i<Math.min(5,Math.max(0,amount));i++) {
      const x=(i%3)*22,y=-Math.floor(i/3)*12;
      s+=rect(x,y,28,12,"#9d7c52")+rect(x+2,y+2,24,3,"#c5a171")+rect(x,y+2,8,8,"#dcc398")+rect(x+2,y+4,4,4,"#ad8c5e");
    }
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="-4 -20 82 40" shape-rendering="crispEdges" aria-hidden="true">${s}</svg>`;
  }
  window.StarhavenForestArt = { scene, cargo, trees };
})();
