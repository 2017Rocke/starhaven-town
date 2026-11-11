"use strict";

// Only stepped shapes on an integer grid, in the established town palette.
(() => {
  const trees = [[940,-350,2],[1040,-394,1],[1130,-370,0],[1230,-414,1],[1448,-404,2],[1550,-396,0],[1700,-370,2],[1810,-326,1],[1050,-212,2],[1150,-130,0],[1500,-150,1],[1630,-170,2],[1740,-106,0],[970,-80,1],[1860,-180,0]];
  const rect = (x,y,w,h,color) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" fill="${color}"/>`;
  const path = (d,color) => `<path d="${d}" fill="${color}"/>`;
  function tree(x,y,size) {
    const unit = size + 2;
    const colors = size === 1 ? ["#6d8b59","#88a46c","#a0b77d","#b7c98d"] : ["#708952","#8fa66a","#a4b67c","#c1cf92"];
    return `<g data-forest-tree="${size}" transform="translate(${x} ${y}) scale(${unit})">` + rect(-10,15,22,3,"#91a674") + rect(-2,-4,4,21,"#927c54") + rect(1,0,2,16,"#7b6c49") + path("M-12-7h-3v-8h3v-5h5v-4H4v3h5v4h5v10h-3v6H3v3h-9v-3h-6Z",colors[0]) + path("M-12-14h3v-5h5v-3H3v3h5v4h4v6H7v5H-2v-2h-7v-3h-3Z",colors[1]) + path("M-8-15h4v-4H3v3h5v4H3v3H-4v-3h-4Z",colors[2]) + rect(-3,-19,4,2,colors[3]) + rect(-10,-9,2,3,colors[2]) + rect(7,-5,3,2,colors[2]) + "</g>";
  }
  function bench(x,y) {
    return `<g transform="translate(${x} ${y})">`+rect(0,0,54,8,"#b69a6e")+rect(0,10,54,8,"#ccb58b")+rect(-3,21,60,7,"#b69a6e")+rect(5,28,5,13,"#7c7958")+rect(45,28,5,13,"#7c7958")+rect(0,0,54,2,"#deca9f")+"</g>";
  }
  function lamp(x,y) {
    return `<g transform="translate(${x} ${y})">`+rect(-3,-48,6,50,"#7d886b")+rect(-7,0,14,4,"#8a9273")+rect(-11,-66,22,5,"#718267")+rect(-8,-61,16,16,"#c2b582")+rect(-5,-58,10,10,"#f4dca0")+rect(-5,-71,10,5,"#829174")+"</g>";
  }
  function scene() {
    let s = path("M870-408h90v-36h330v-26h310v22h250v44h96v260h-56v112h-232v30h-410v-24H940v-90h-70Z","#cfdfad");
    s += path("M920-384h140v-34h200v-20h300v18h230v34h116v220h-56v100h-244v20h-350v-20H986v-100h-66Z","#d3e2b4");
    // A narrow avenue through the 46px gap between existing northern plots.
    s += rect(1367,-96,26,383,"#c9c69e") + rect(1371,-96,18,383,"#e7ddbb");
    s += rect(1367,287,66,26,"#c9c69e") + rect(1371,291,58,18,"#e7ddbb");
    s += rect(1407,300,26,30,"#c9c69e") + rect(1411,300,18,30,"#e7ddbb");
    // Park loop and three short collection spurs, kept clear of tree trunks.
    const tracks = [[1138,-348,524,18],[1138,-340,18,266],[1644,-340,18,266],[1138,-92,524,18],[1312,-220,16,136],[1228,-228,188,16]];
    for (const [x,y,w,h] of tracks) s += rect(x,y,w,h,"#cabb92") + rect(x+3,y+3,w-6,h-6,"#dfd1a9");
    for (const [x,y] of [[1238,-220],[1320,-220],[1402,-220]]) s += rect(x-19,y-14,38,28,"#dfd1a9")+rect(x-13,y+10,10,2,"#cbbb91");
    s += rect(1358,-110,44,28,"#e7ddbb");
    s += bench(1510,-62) + bench(1184,-304) + lamp(1346,-54) + lamp(1418,282) + lamp(1678,-70);
    s += `<g transform="translate(1434 -48)">`+rect(0,-22,6,58,"#927c54")+rect(74,-22,6,58,"#927c54")+rect(-6,-30,92,35,"#9e875f")+rect(-3,-27,86,29,"#dcc69a")+rect(5,-20,68,3,"#efe0bb")+`</g>`;
    for (const [x,y,size] of trees) s += tree(x,y,size);
    // Small understory details leave broad readable negative space.
    for (const [x,y] of [[1010,-300],[1790,-242],[1210,-58],[1540,-286],[1560,-288],[1474,-322],[1088,-108]]) s += rect(x,y,4,12,"#91a573")+rect(x-4,y-2,12,6,"#d8bd87")+rect(x,y-4,4,10,"#e9d49b");
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
