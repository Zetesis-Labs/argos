import { loadQuartzConfig, loadQuartzLayout } from "./quartz/plugins/loader/config-loader"
import { componentRegistry } from "./quartz/components/registry"
import { profile, explorer } from "./okf.config.mjs"

componentRegistry.setOptionOverrides("quartz-okf", { profile })
componentRegistry.setOptionOverrides("quartz-okf-explorer", explorer)

const config = await loadQuartzConfig()
export default config
export const layout = await loadQuartzLayout()
