/**
 * @file Vite plugin to inline CSS imports
 * @import { Plugin as VitePlugin } from "vite";
 */

import { readFileSync } from "node:fs";

const CSSImportPattern =
    /import\s+([\w$]+)\s+from\s+(["'])([^"']+\.css)(\?lit-css-text)?\2(?:\s+with\s+\{\s*type:\s*["']bundled-text["']\s*\})?/g;
const JavaScriptFilePattern = /\.m?(js|ts|tsx)(?:\?.*)?$/;
const CSSQuery = "?lit-css-text";
const CSSVirtualModulePrefix = "\0lit-css-text:";

export function inlineCSSPlugin() {
    const virtualModules = new Map();

    /**
     * @satisfies {VitePlugin}
     */
    const inlineCSSPlugin = {
        name: "inline-css-plugin",
        enforce: "pre",
        resolveId: async function (source, importer) {
            if (!source.endsWith(CSSQuery)) return;

            const resolved = await this.resolve(source.slice(0, -CSSQuery.length), importer, {
                skipSelf: true,
            });

            if (!resolved) return;

            const virtualId = `${CSSVirtualModulePrefix}${virtualModules.size}`;
            virtualModules.set(virtualId, resolved.id);

            return virtualId;
        },
        load: (id) => {
            if (!id.startsWith(CSSVirtualModulePrefix)) return;

            const path = virtualModules.get(id);
            if (!path) return;

            const css = readFileSync(path, "utf8");

            return {
                code: `export default ${JSON.stringify(css)};`,
            };
        },
        transform: (source, id) => {
            if (!JavaScriptFilePattern.test(id)) return;

            const code = source.replace(CSSImportPattern, (_match, name, quote, path, query) => {
                return `import ${name} from ${quote}${path}${query ?? CSSQuery}${quote}`;
            });

            return {
                code,
            };
        },
    };

    return inlineCSSPlugin;
}
