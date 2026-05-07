import { Component } from "react";

// 213: app-wide ErrorBoundary so a single tab/card crash doesn't drop the
// operator into a blank screen. Production shows a generic message + 새로고침
// hint; dev mode (import.meta.env.DEV) also prints error.message + the
// component stack so the cause is visible without flipping to devtools.
//
// Why a class component: React still requires `componentDidCatch` /
// `getDerivedStateFromError` for catching render-time errors. Function
// components can't replace this.
export class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null, info: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    this.setState({ info });
    if (typeof console !== "undefined") {
      console.error("ErrorBoundary caught:", error, info);
    }
  }

  _reset = () => this.setState({ error: null, info: null });

  render() {
    const { error, info } = this.state;
    if (!error) return this.props.children;

    const isDev = (typeof import.meta !== "undefined") && import.meta.env?.DEV;
    const label = this.props.label || "화면";

    return (
      <div
        data-testid="error-boundary"
        style={{
          padding: 16, margin: 12, borderRadius: 8,
          background: "#1a0e0e", border: "1px solid #ef444466",
          color: "#fca5a5", fontSize: 12, lineHeight: 1.5,
        }}
      >
        <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 6 }}>
          ⚠️ {label}에서 오류가 발생했습니다.
        </div>
        <div style={{ color: "#94a3b8", marginBottom: 10 }}>
          페이지 새로고침(Ctrl+R) 후 다시 시도해 주세요. 동일 오류가 반복되면 백엔드
          로그를 확인하거나 운영자에게 문의하세요.
        </div>
        {isDev && (
          <pre data-testid="error-boundary-detail"
               style={{
                 background: "#0c2035", padding: 8, borderRadius: 4,
                 color: "#fbbf24", fontSize: 10, whiteSpace: "pre-wrap",
                 wordBreak: "break-word", maxHeight: 200, overflow: "auto",
               }}>
            {error?.message || String(error)}
            {info?.componentStack ? "\n" + info.componentStack : ""}
          </pre>
        )}
        <button
          type="button"
          onClick={this._reset}
          data-testid="error-boundary-reset"
          style={{
            marginTop: 10, padding: "6px 12px",
            background: "#0c2035", border: "1px solid #ef444466",
            borderRadius: 4, color: "#fca5a5", cursor: "pointer",
            fontFamily: "inherit", fontSize: 11,
          }}
        >
          다시 시도
        </button>
      </div>
    );
  }
}
