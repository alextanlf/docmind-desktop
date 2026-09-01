import { Component, type ErrorInfo, type ReactNode } from "react";
import { RefreshCw, TriangleAlert } from "lucide-react";

type Props = { children: ReactNode };
type State = { failed: boolean };

export class ErrorBoundary extends Component<Props, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Renderer boundary caught an error", error.name, info.componentStack);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <main className="fatal-view" role="alert">
        <TriangleAlert aria-hidden="true" size={24} />
        <h1>应用界面发生错误</h1>
        <p>请重新加载应用。如果问题持续出现，请检查本地日志。</p>
        <button className="button button-primary" onClick={() => window.location.reload()}>
          <RefreshCw aria-hidden="true" size={16} />
          重新加载应用
        </button>
      </main>
    );
  }
}
