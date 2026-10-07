/**
 * Vite 的 `?url` 导入在 tsc 下没有类型（tsconfig 未引入 `vite/client`）。
 * 只补这一条，避免把 vite 的全部全局声明拉进来。
 */
declare module "*?url" {
  const source: string;
  export default source;
}
