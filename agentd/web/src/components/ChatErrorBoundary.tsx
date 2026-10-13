import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  onReset?: () => void;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

/**
 * React Error Boundary：包住消息列表/流式渲染区，组件抛错时
 * 渲染降级 UI 而非让整个应用白屏。用户可点「重试」尝试恢复。
 */
export class ChatErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("[ChatErrorBoundary] 渲染异常:", error, info.componentStack);
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null });
    this.props.onReset?.();
  };

  render() {
    if (this.state.hasError) {
      return (
        <div className="err-boundary" role="alert">
          <div className="err-boundary-card">
            <p className="err-boundary-title">页面渲染出错</p>
            <p className="err-boundary-msg">{this.state.error?.message || "未知错误"}</p>
            <button className="btn primary" onClick={this.handleRetry}>
              重试
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
