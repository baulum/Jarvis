// Top-bar pill in the 3D view: model, effort and the Claude quota (see components/ModelPicker).
import { ModelPicker } from "../components/ModelPicker";

export function ModelDock() {
  return <ModelPicker placement="down" showQuota />;
}
