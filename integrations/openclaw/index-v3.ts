import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

import v21Plugin from "./index-v2.ts";
import { registerExtendedMemoryRuntime } from "./v3-memory-extended.ts";
import { registerV3Runtime } from "./v3-runtime.ts";

const basePlugin = v21Plugin as any;

export default definePluginEntry({
  id: "yogurt-social-injector",
  name: "Yogurt Social Injector",
  description:
    "Injects Yogurt social context, delivers bounded V2.1 stickers, and provides opt-in V3 memory and multi-bubble experiments.",
  register(api: any) {
    basePlugin.register(api);
    registerV3Runtime(api);
    registerExtendedMemoryRuntime(api);
  },
});
