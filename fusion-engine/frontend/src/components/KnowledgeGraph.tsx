import { useEffect, useRef } from "react";
import * as d3 from "d3";
import type { Graph, GraphLink, GraphNode } from "../lib/types";

const colors: Record<string, string> = { event: "#eab85a", aircraft: "#5cc7da", actor: "#b294d4", location: "#94c973", source: "#7b887a" };
type SimulationLink = Omit<GraphLink, "source" | "target"> & { source: string | GraphNode; target: string | GraphNode };

function coordinate(endpoint: string | GraphNode, axis: "x" | "y") {
  return typeof endpoint === "string" ? 0 : endpoint[axis] ?? 0;
}

export function KnowledgeGraph({ graph, onSelect }: { graph: Graph; onSelect: (node: GraphNode) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = ref.current;
    if (!element || !graph.nodes.length) return;
    const width = element.clientWidth; const height = element.clientHeight;
    const nodes = graph.nodes.map((node) => ({ ...node }));
    const ids = new Set(nodes.map((node) => node.id));
    const links: SimulationLink[] = graph.links.filter((link) => ids.has(link.source) && ids.has(link.target)).map((link) => ({ ...link }));
    const svg = d3.select(element).append("svg").attr("width", width).attr("height", height);
    const root = svg.append("g");
    svg.call(d3.zoom<SVGSVGElement, unknown>().scaleExtent([.2, 6]).on("zoom", (event) => root.attr("transform", event.transform)));
    const simulation = d3.forceSimulation<GraphNode>(nodes)
      .force("link", d3.forceLink<GraphNode, SimulationLink>(links).id((node) => node.id).distance((link) => link.kind === "NEAR" ? 40 : 25).strength(.5))
      .force("charge", d3.forceManyBody().strength(-60)).force("center", d3.forceCenter(width / 2, height / 2)).force("collide", d3.forceCollide(7));
    const edge = root.append("g").selectAll<SVGLineElement, SimulationLink>("line").data(links).join("line").attr("stroke", (link) => link.kind === "NEAR" ? "#df5e55" : "#435142").attr("stroke-width", (link) => link.kind === "NEAR" ? 1 + 3 * (link.score ?? 0) : .6).attr("stroke-opacity", .7);
    const node = root.append("g").selectAll<SVGCircleElement, GraphNode>("circle").data(nodes).join("circle")
      .attr("r", (item) => item.kind === "actor" ? 4 + Math.min(item.mentions ?? 0, 20) / 3 : item.kind === "event" ? 4 + 6 * (item.severity ?? 0) : 4)
      .attr("fill", (item) => item.kind === "aircraft" && item.military ? "#df5e55" : colors[item.kind] || "#a7aa9c").attr("stroke", "#101710").attr("stroke-width", .8)
      .style("cursor", "pointer").on("click", (_event, item) => onSelect(item))
      .call(d3.drag<SVGCircleElement, GraphNode>()
        .on("start", (event, item) => { if (!event.active) simulation.alphaTarget(.3).restart(); item.fx = item.x; item.fy = item.y; })
        .on("drag", (event, item) => { item.fx = event.x; item.fy = event.y; })
        .on("end", (event, item) => { if (!event.active) simulation.alphaTarget(0); item.fx = null; item.fy = null; }));
    node.append("title").text((item) => `${item.kind}: ${item.label}`);
    simulation.on("tick", () => {
      edge.attr("x1", (item) => coordinate(item.source, "x")).attr("y1", (item) => coordinate(item.source, "y")).attr("x2", (item) => coordinate(item.target, "x")).attr("y2", (item) => coordinate(item.target, "y"));
      node.attr("cx", (item) => item.x ?? 0).attr("cy", (item) => item.y ?? 0);
    });
    // A force layout is useful for orientation, but must never continuously occupy the UI thread.
    const stopTimer = window.setTimeout(() => simulation.stop(), 1500);
    return () => { window.clearTimeout(stopTimer); simulation.stop(); svg.remove(); };
  }, [graph, onSelect]);
  return <div ref={ref} className="h-full w-full" aria-label="Knowledge graph" />;
}
