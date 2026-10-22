import { Component, type ReactNode } from "react";
import Icon from "./Icon";

type Props = { children: ReactNode; label?: string };
type State = { error: Error | null };

/** Keeps one broken section from blanking the whole page. */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="notice bad" role="alert">
        <Icon name="alert" />
        <div className="grow">
          <div className="strong">{this.props.label ?? "This part of the page"} hit a problem.</div>
          <div className="small" style={{ marginTop: 2 }}>
            {this.state.error.message}. If you just updated JobBee, restart the backend, then
            reload.
          </div>
          <button className="sm" style={{ marginTop: 8 }} onClick={() => window.location.reload()}>
            Reload
          </button>
        </div>
      </div>
    );
  }
}
