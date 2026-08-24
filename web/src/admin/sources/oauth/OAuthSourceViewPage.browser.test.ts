import { beforeEach, describe, expect, it, vi } from "vitest";

const dingTalkPanelImports = vi.hoisted(() => ({
    allowlist: vi.fn(),
    directory: vi.fn(),
    directorySources: vi.fn(),
}));

const ProviderTypeEnum = {
    Apple: "apple",
    Openidconnect: "openidconnect",
    Entraid: "entraid",
    Dingtalk: "dingtalk",
    Discord: "discord",
    Facebook: "facebook",
    Github: "github",
    Gitlab: "gitlab",
    Google: "google",
    Mailcow: "mailcow",
    Okta: "okta",
    Patreon: "patreon",
    Reddit: "reddit",
    Slack: "slack",
    Twitch: "twitch",
    Twitter: "twitter",
    Wechat: "wechat",
    UnknownDefaultOpenApi: "11184809",
} as const;

type ProviderTypeEnum = (typeof ProviderTypeEnum)[keyof typeof ProviderTypeEnum];

interface OAuthSourceFixture {
    pk: string;
    slug: string;
    name: string;
    providerType: ProviderTypeEnum;
    callbackUrl: string;
    consumerKey: string;
    authorizationUrl: string;
    accessTokenUrl: string;
}

interface OAuthSourceViewPageElement extends HTMLElement {
    source?: OAuthSourceFixture;
    updateComplete: Promise<unknown>;
}

vi.mock("@goauthentik/api", () => ({
    AdminApi: class AdminApi {},
    CapabilitiesEnum: {
        CanDebug: "can_debug",
    },
    Configuration: class Configuration {
        public constructor(init?: object) {
            Object.assign(this, init);
        }
    },
    FlowLayoutEnum: {
        ContentLeft: "content_left",
        ContentRight: "content_right",
        Stacked: "stacked",
    },
    instanceOfValidationError: () => false,
    LicenseFlagsEnum: {
        NonProduction: "non_production",
        Trial: "trial",
    },
    LicenseSummaryStatusEnum: {
        Expired: "expired",
        ExpirySoon: "expiry_soon",
        LimitExceededAdmin: "limit_exceeded_admin",
        LimitExceededUser: "limit_exceeded_user",
        ReadOnly: "read_only",
        Unlicensed: "unlicensed",
        Valid: "valid",
    },
    ModelEnum: {
        AuthentikSourcesOauthOauthsource: "authentik_sources_oauth.oauthsource",
    },
    ProviderTypeEnum,
    SourcesApi: class SourcesApi {
        public async sourcesOauthRetrieve(): Promise<never> {
            throw new Error("sourcesOauthRetrieve should not be called in this browser test");
        }
    },
    EnterpriseApi: class EnterpriseApi {},
    UiThemeEnum: {
        Automatic: "automatic",
        Dark: "dark",
        Light: "light",
    },
}));

vi.mock("#common/api/client", () => ({
    aki: (APIClass: new () => unknown) => new APIClass(),
}));

vi.mock("#common/global", () => ({
    docLink: (url: string | URL) => String(url),
    globalAK: () =>
        (
            window as unknown as {
                authentik?: unknown;
            }
        ).authentik ?? {
            api: {
                base: "/",
                relBase: "/",
            },
            brand: {
                uiFooterLinks: [],
            },
            build: "",
            config: {
                capabilities: [],
            },
            locale: "en",
            versionFamily: "",
            versionSubdomain: "",
        },
}));

vi.mock("#common/errors/network", () => ({
    parseAPIResponseError: async (error: unknown) => error,
    pluckErrorDetail: (error: unknown) => (error instanceof Error ? error.message : String(error)),
}));

vi.mock("#elements/messages/MessageContainer", () => ({
    showMessage: vi.fn(),
}));

vi.mock("#admin/sources/oauth/OAuthSourceForm", () => ({
    OAuthSourceForm: class OAuthSourceForm extends HTMLElement {},
}));

vi.mock("#elements/sources/utils", () => ({
    sourceBindingTypeNotices: () => [],
}));

vi.mock("#admin/sources/oauth/DingTalkAllowlistPanel", () => {
    dingTalkPanelImports.allowlist();
    if (!customElements.get("ak-source-oauth-dingtalk-allowlist")) {
        customElements.define(
            "ak-source-oauth-dingtalk-allowlist",
            class DingTalkAllowlistPanelStub extends HTMLElement {
                connectedCallback(): void {
                    if (!this.shadowRoot) {
                        this.attachShadow({ mode: "open" }).innerHTML =
                            `<label for="draft-label">Company label</label><input id="draft-label" />`;
                    }
                }
            },
        );
    }
    return {};
});

vi.mock("#admin/sources/oauth/DingTalkDirectoryPanel", () => {
    dingTalkPanelImports.directory();
    if (!customElements.get("ak-source-oauth-dingtalk-directory")) {
        customElements.define(
            "ak-source-oauth-dingtalk-directory",
            class DingTalkDirectoryPanelStub extends HTMLElement {
                set source(value: OAuthSourceFixture | undefined) {
                    dingTalkPanelImports.directorySources(value);
                }
            },
        );
    }
    return {};
});

vi.mock("#admin/sources/oauth/OAuthSourceDiagram", () => {
    if (!customElements.get("ak-source-oauth-diagram")) {
        customElements.define(
            "ak-source-oauth-diagram",
            class OAuthSourceDiagramStub extends HTMLElement {},
        );
    }
    return {};
});

vi.mock("#admin/events/ObjectChangelog", () => {
    if (!customElements.get("ak-object-changelog")) {
        customElements.define(
            "ak-object-changelog",
            class ObjectChangelogStub extends HTMLElement {},
        );
    }
    return {};
});

vi.mock("#admin/policies/BoundPoliciesList", () => {
    if (!customElements.get("ak-bound-policies-list")) {
        customElements.define(
            "ak-bound-policies-list",
            class BoundPoliciesListStub extends HTMLElement {},
        );
    }
    return {};
});

vi.mock("#admin/rbac/ak-rbac-object-permission-page", () => {
    if (!customElements.get("ak-rbac-object-permission-page")) {
        customElements.define(
            "ak-rbac-object-permission-page",
            class RBACObjectPermissionPageStub extends HTMLElement {},
        );
    }
    return {};
});

function setAuthentikGlobal(): void {
    (
        window as unknown as {
            authentik: unknown;
        }
    ).authentik = {
        brand: {
            matched_domain: "",
            branding_title: "authentik",
            branding_logo: "",
            branding_logo_themed_urls: {},
            branding_favicon: "",
            branding_favicon_themed_urls: {},
            branding_custom_css: "",
            ui_footer_links: [],
            ui_theme: "automatic",
            default_locale: "en",
            flags: {
                core_default_app_access: false,
                enterprise_audit_include_expanded_diff: false,
                flows_continuous_login: false,
                flows_refresh_others: false,
            },
        },
        api: {
            base: "/",
            relBase: "/",
        },
        config: {
            error_reporting: {
                enabled: false,
                sentry_dsn: "",
                environment: "",
                send_pii: false,
                traces_sample_rate: 0,
            },
            capabilities: [],
            cache_timeout: 0,
            cache_timeout_flows: 0,
            cache_timeout_policies: 0,
        },
        locale: "en",
        versionFamily: "",
        versionSubdomain: "",
        build: "",
    };
}

function makeSource(providerType: ProviderTypeEnum): OAuthSourceFixture {
    return {
        pk: "source-pk",
        slug: "dingtalk",
        name: "DingTalk",
        providerType,
        callbackUrl: "https://authentik.example/source/oauth/callback/dingtalk/",
        consumerKey: "consumer-key",
        authorizationUrl: "https://login.dingtalk.example/authorize",
        accessTokenUrl: "https://login.dingtalk.example/token",
    };
}

async function makeSourceView(providerType?: ProviderTypeEnum) {
    setAuthentikGlobal();
    await import("#admin/sources/oauth/OAuthSourceViewPage");
    const view = document.createElement("ak-source-oauth-view") as OAuthSourceViewPageElement;

    if (providerType) {
        view.source = makeSource(providerType);
    }

    document.body.append(view);
    await view.updateComplete;
    await view.updateComplete;

    return view;
}

function tabButton(view: HTMLElement, tabName: string): HTMLButtonElement {
    const tabs = view.shadowRoot?.querySelector("ak-tabs");
    const button = tabs?.shadowRoot?.querySelector<HTMLButtonElement>(`button[name=${tabName}]`);

    if (!button) {
        throw new Error(`Tab button ${tabName} not found`);
    }

    return button;
}

async function waitForTabButton(view: HTMLElement, tabName: string): Promise<HTMLButtonElement> {
    let button: HTMLButtonElement | undefined;

    await vi.waitFor(() => {
        button = tabButton(view, tabName);
    });

    return button!;
}

describe("OAuthSourceViewPage", () => {
    beforeEach(() => {
        dingTalkPanelImports.allowlist.mockClear();
        dingTalkPanelImports.directory.mockClear();
        dingTalkPanelImports.directorySources.mockClear();
        document.body.replaceChildren();
    });

    it("renders an initial loading state before the source is loaded", async () => {
        const view = await makeSourceView();

        expect(view.shadowRoot?.querySelector("ak-empty-state[loading]")).toBeTruthy();
    });

    it("does not create DingTalk tabs or load DingTalk modules for other OAuth providers", async () => {
        const view = await makeSourceView(ProviderTypeEnum.Google);

        expect(view.shadowRoot?.querySelector("[slot=page-dingtalk-allowlist]")).toBeNull();
        expect(view.shadowRoot?.querySelector("[slot=page-dingtalk-directory]")).toBeNull();
        expect(dingTalkPanelImports.allowlist).not.toHaveBeenCalled();
        expect(dingTalkPanelImports.directory).not.toHaveBeenCalled();
    });

    it("loads DingTalk modules on first DingTalk tab activation", async () => {
        const view = await makeSourceView(ProviderTypeEnum.Dingtalk);

        expect(view.shadowRoot?.querySelector("[slot=page-dingtalk-allowlist]")).toBeTruthy();
        expect(view.shadowRoot?.querySelector("[slot=page-dingtalk-directory]")).toBeTruthy();
        expect(view.shadowRoot?.querySelector("ak-source-oauth-dingtalk-allowlist")).toBeNull();
        expect(view.shadowRoot?.querySelector("ak-source-oauth-dingtalk-directory")).toBeNull();
        expect(dingTalkPanelImports.allowlist).not.toHaveBeenCalled();
        expect(dingTalkPanelImports.directory).not.toHaveBeenCalled();

        (await waitForTabButton(view, "page-dingtalk-directory")).click();

        await vi.waitFor(() => {
            expect(dingTalkPanelImports.allowlist).toHaveBeenCalledTimes(1);
            expect(dingTalkPanelImports.directory).toHaveBeenCalledTimes(1);
            expect(
                view.shadowRoot?.querySelector("ak-source-oauth-dingtalk-directory"),
            ).toBeTruthy();
        });

        expect(view.shadowRoot?.querySelector("ak-source-oauth-dingtalk-allowlist")).toBeNull();

        (await waitForTabButton(view, "page-dingtalk-allowlist")).click();

        await vi.waitFor(() => {
            expect(
                view.shadowRoot?.querySelector("ak-source-oauth-dingtalk-allowlist"),
            ).toBeTruthy();
        });

        expect(view.shadowRoot?.querySelector("ak-source-oauth-dingtalk-directory")).toBeTruthy();
        expect(dingTalkPanelImports.allowlist).toHaveBeenCalledTimes(1);
        expect(dingTalkPanelImports.directory).toHaveBeenCalledTimes(1);
        expect(dingTalkPanelImports.directorySources).toHaveBeenLastCalledWith(undefined);
    });

    it("preserves an unsaved allowlist draft across DingTalk tab switches", async () => {
        const view = await makeSourceView(ProviderTypeEnum.Dingtalk);

        (await waitForTabButton(view, "page-dingtalk-allowlist")).click();

        await vi.waitFor(() => {
            expect(
                view.shadowRoot?.querySelector("ak-source-oauth-dingtalk-allowlist"),
            ).toBeTruthy();
        });

        const allowlist = view.shadowRoot?.querySelector("ak-source-oauth-dingtalk-allowlist");
        const draftInput = allowlist?.shadowRoot?.querySelector<HTMLInputElement>("#draft-label");

        expect(draftInput).toBeTruthy();
        draftInput!.value = "Unsaved company label";
        draftInput!.dispatchEvent(new InputEvent("input", { bubbles: true }));

        (await waitForTabButton(view, "page-dingtalk-directory")).click();

        await vi.waitFor(() => {
            expect(
                view.shadowRoot?.querySelector("ak-source-oauth-dingtalk-directory"),
            ).toBeTruthy();
        });

        (await waitForTabButton(view, "page-dingtalk-allowlist")).click();

        await vi.waitFor(() => {
            expect(tabButton(view, "page-dingtalk-allowlist").ariaSelected).toBe("true");
        });

        const retainedAllowlist = view.shadowRoot?.querySelector(
            "ak-source-oauth-dingtalk-allowlist",
        );
        const retainedDraftInput =
            retainedAllowlist?.shadowRoot?.querySelector<HTMLInputElement>("#draft-label");

        expect(retainedAllowlist).toBe(allowlist);
        expect(retainedDraftInput?.value).toBe("Unsaved company label");
    });
});
