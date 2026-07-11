import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import type { OAuthSourceViewPage } from "#admin/sources/oauth/OAuthSourceViewPage";

import { type OAuthSource, ProviderTypeEnum } from "@goauthentik/api";

import { beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("#admin/events/ObjectChangelog", () => ({}));
vi.mock("#admin/policies/BoundPoliciesList", () => ({}));
vi.mock("#admin/rbac/ak-rbac-object-permission-page", () => ({}));
vi.mock("#admin/sources/oauth/DingTalkAllowlistPanel", () => ({}));
vi.mock("#admin/sources/oauth/DingTalkDirectoryPanel", () => ({}));
vi.mock("#admin/sources/oauth/OAuthSourceDiagram", () => ({}));
vi.mock("#elements/CodeMirror", () => ({}));
vi.mock("#elements/EmptyState", () => ({}));
vi.mock("#elements/Tabs", () => ({}));
vi.mock("#elements/buttons/SpinnerButton/index", () => ({}));
vi.mock("#admin/sources/oauth/OAuthSourceForm", () => ({
    OAuthSourceForm: class OAuthSourceForm {},
}));
vi.mock("lex", () => ({
    Lexer: class Lexer {},
}));

let OAuthSourceViewPageCtor: typeof OAuthSourceViewPage;

function renderSourceView(providerType: ProviderTypeEnum): string {
    const page = new OAuthSourceViewPageCtor();
    page.source = {
        pk: "source-pk",
        slug: "dingtalk",
        name: "DingTalk",
        providerType,
        callbackUrl: "https://authentik.example/source/oauth/callback/dingtalk/",
        consumerKey: "consumer-key",
        authorizationUrl: "https://login.dingtalk.example/authorize",
        accessTokenUrl: "https://login.dingtalk.example/token",
    } as OAuthSource;

    return JSON.stringify(page.render());
}

describe("OAuthSourceViewPage", () => {
    beforeAll(async () => {
        globalThis.CSSStyleSheet ??= class CSSStyleSheet {
            replaceSync(): void {
                return undefined;
            }
        } as unknown as typeof CSSStyleSheet;
        globalThis.HTMLElement ??= class HTMLElement {
            addEventListener(): void {
                return undefined;
            }
            removeEventListener(): void {
                return undefined;
            }
            dispatchEvent(): boolean {
                return true;
            }
        } as unknown as typeof HTMLElement;
        globalThis.HTMLIFrameElement ??=
            class HTMLIFrameElement extends HTMLElement {} as typeof HTMLIFrameElement;
        globalThis.customElements ??= {
            define: () => {},
            get: () => undefined,
            whenDefined: async () => undefined,
        } as unknown as CustomElementRegistry;
        const windowStub = {
            location: {
                origin: "http://localhost",
                search: "",
            },
            addEventListener: () => {},
            removeEventListener: () => {},
        } as unknown as Window & typeof globalThis;
        globalThis.window ??= windowStub;
        globalThis.self ??= windowStub;
        ({ OAuthSourceViewPage: OAuthSourceViewPageCtor } =
            await import("#admin/sources/oauth/OAuthSourceViewPage"));
    });

    it("renders the DingTalk allowlist panel before the DingTalk directory panel", () => {
        const rendered = renderSourceView(ProviderTypeEnum.Dingtalk);

        const allowlistIndex = rendered.indexOf("ak-source-oauth-dingtalk-allowlist");
        const directoryIndex = rendered.indexOf("ak-source-oauth-dingtalk-directory");
        expect(allowlistIndex).toBeGreaterThan(-1);
        expect(directoryIndex).toBeGreaterThan(-1);
        expect(allowlistIndex).toBeLessThan(directoryIndex);
    });

    it("does not render DingTalk-only panels for other OAuth providers", () => {
        const rendered = renderSourceView(ProviderTypeEnum.Google);

        expect(rendered).not.toContain("ak-source-oauth-dingtalk-allowlist");
        expect(rendered).not.toContain("ak-source-oauth-dingtalk-directory");
    });

    it("uses stable localized labels for DingTalk tabs", () => {
        const source = readFileSync(
            resolve(import.meta.dirname, "../../src/admin/sources/oauth/OAuthSourceViewPage.ts"),
            "utf8",
        );
        const zhHans = readFileSync(
            resolve(import.meta.dirname, "../../xliff/zh-Hans.xlf"),
            "utf8",
        );

        for (const id of [
            "sources.oauth.dingtalk-allowlist.tab",
            "sources.oauth.dingtalk-directory.tab",
        ]) {
            expect(source).toContain(`id: "${id}"`);
            expect(zhHans).toContain(`<trans-unit id="${id}">`);
        }
    });

    it("invalidates stale route requests and exposes a retryable error state", () => {
        const source = readFileSync(
            resolve(import.meta.dirname, "../../src/admin/sources/oauth/OAuthSourceViewPage.ts"),
            "utf8",
        );

        expect(source).toContain("private requestGeneration = 0;");
        expect(source).toContain("const generation = ++this.requestGeneration;");
        expect(source).toContain("generation === this.requestGeneration");
        expect(source).toContain("this.source = undefined;");
        expect(source).toContain('id: "sources.oauth.view.error.load"');
    });
});
