import { useState } from "react";
import { useGraphAdjacencyQuery, useGraphNodesQuery } from "../repositories/repository.queries";

export function GraphView() {
  const nodes = useGraphNodesQuery();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const adjacency = useGraphAdjacencyQuery(selectedId);

  if (nodes.isPending) return <p>正在读取图谱…</p>;
  if (!nodes.data || nodes.data.length === 0) return <p>暂无图谱数据</p>;

  return (
    <div className="graph-view">
      <ul aria-label="图谱节点">
        {nodes.data.map((node) => (
          <li key={node.id}>
            <button onClick={() => setSelectedId(node.id)} type="button">
              {node.label}
            </button>
          </li>
        ))}
      </ul>
      {selectedId && adjacency.data ? (
        <ul aria-label="相邻关系">
          {adjacency.data.map((edge) => (
            <li key={`${edge.sourceId}-${edge.targetId}-${edge.relation}`}>{edge.relation}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
