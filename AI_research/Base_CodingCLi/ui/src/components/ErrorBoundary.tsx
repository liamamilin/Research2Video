import { Component, type ReactNode } from "react";
import { isChunkError, recoverChunk } from "@/lib/page-recovery";

interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: unknown) {
    console.error("[ui] unhandled render error:", error, info);
    recoverChunk(error);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex h-screen items-center justify-center bg-bg p-6">
          <div className="card p-6 max-w-lg w-full space-y-3 text-center">
            <div className="text-lg font-semibold text-danger">{isChunkError(this.state.error) ? "页面更新暂未载入" : "页面运行出错"}</div>
            {isChunkError(this.state.error) ? <p className="text-sm text-text-muted">资源仍未载入成功，已保留当前地址和本机暂存。检查连接后刷新即可继续。</p> : null}
            <div className="text-xs text-text-muted font-mono break-all">
              {this.state.error.message}
            </div>
            <div className="flex gap-2 justify-center">
              <button
                className="btn btn-primary text-xs"
                onClick={() => { window.dispatchEvent(new Event("ui-before-reload")); window.location.reload(); }}
              >
                刷新页面
              </button>
              {!isChunkError(this.state.error) ? <button
                className="btn text-xs"
                onClick={() => this.setState({ error: null })}
              >
                重试
              </button> : null}
            </div>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
