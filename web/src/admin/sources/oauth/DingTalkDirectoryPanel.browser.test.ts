import type { DingTalkDirectoryClient } from "./DingTalkDirectoryApi";
import type { DingTalkDirectoryPanel } from "./DingTalkDirectoryPanel";
import type { DingTalkDirectorySyncStatus } from "./DingTalkDirectoryPanelController";

import { MessageLevel } from "#common/messages";

import { beforeEach, describe, expect, it, vi } from "vitest";

const showMessage = vi.hoisted(() => vi.fn());

vi.mock("#elements/messages/MessageContainer", () => ({
    showMessage,
}));

vi.mock("#elements/tasks/ScheduleList", () => {
    if (!customElements.get("ak-schedule-list")) {
        customElements.define("ak-schedule-list", class ScheduleList extends HTMLElement {});
    }
    return {};
});

vi.mock("#common/api/config", () => ({
    DEFAULT_CONFIG: {},
}));

vi.mock("#common/errors/network", () => ({
    parseAPIResponseError: async (error: unknown) => error,
    pluckErrorDetail: (error: unknown) => (error instanceof Error ? error.message : String(error)),
}));

vi.mock("@goauthentik/api", () => ({
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
    SourcesApi: class SourcesApi {},
    UiThemeEnum: {
        Automatic: "automatic",
        Dark: "dark",
        Light: "light",
    },
}));

vi.mock("#common/global", () => ({
    docLink: (url: string | URL) => String(url),
    globalAK: () => ({
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
    }),
}));

vi.mock("#common/sentry/index", () => ({
    SentryIgnoredError: class SentryIgnoredError extends Error {},
}));

vi.mock("#elements/entities/used-by", () => ({
    usedByLabel: () => "",
}));

function makeSyncStatus(
    corpId: string,
    status: string,
    overrides: Partial<DingTalkDirectorySyncStatus> = {},
): DingTalkDirectorySyncStatus {
    return {
        corpId,
        status,
        startedAt: null,
        finishedAt: null,
        error: "",
        counters: {},
        ...overrides,
    };
}

async function registerDingTalkDirectoryPanel(): Promise<void> {
    await import("./DingTalkDirectoryPanel");
}

function makePanel(client: Partial<DingTalkDirectoryClient>): DingTalkDirectoryPanel {
    const panel = document.createElement(
        "ak-source-oauth-dingtalk-directory",
    ) as DingTalkDirectoryPanel;

    Object.assign(panel, {
        api: {
            status: async () => ({ sourceSlug: "source-a", canChange: true, sync: [] }),
            sync: async (_sourceSlug: string, request: { corpId: string }) => ({
                queued: true,
                corpId: request.corpId,
            }),
            destroy: async () => undefined,
            ...client,
        },
        source: { slug: "source-a" },
    });

    document.body.append(panel);
    return panel;
}

async function settled(panel: DingTalkDirectoryPanel): Promise<void> {
    await panel.updateComplete;
    await new Promise((resolve) => setTimeout(resolve, 0));
    await panel.updateComplete;
}

describe("DingTalkDirectoryPanel", () => {
    beforeEach(async () => {
        await registerDingTalkDirectoryPanel();
        vi.mocked(showMessage).mockClear();
        document.body.replaceChildren();
    });

    it("renders loading summary instead of zero metrics before status has loaded", async () => {
        const panel = makePanel({
            status: () => new Promise(() => undefined),
        });

        await panel.updateComplete;

        expect(panel.shadowRoot?.textContent).toContain("Loading DingTalk directory status.");
        expect(panel.shadowRoot?.textContent).not.toContain("Corp sync records");
    });

    it("marks old metrics as stale when an explicit refresh fails after data loaded", async () => {
        let fail = false;
        const panel = makePanel({
            status: async () => {
                if (fail) {
                    throw new Error("network failed");
                }
                return {
                    sourceSlug: "source-a",
                    canChange: true,
                    sync: [makeSyncStatus("corp-a", "success")],
                };
            },
        });
        await settled(panel);
        fail = true;

        const refresh = panel.shadowRoot?.querySelector("ak-spinner-button");
        await expect(refresh!.callAction!()).rejects.toThrow("network failed");
        await settled(panel);

        expect(panel.shadowRoot?.textContent).toContain(
            "Previous directory status is shown because refresh failed.",
        );
        expect(panel.shadowRoot?.textContent).toContain("corp-a");
    });

    it("submits Enter and button paths through one pending sync action", async () => {
        let syncCalls = 0;
        let resolveSync: (() => void) | undefined;
        const panel = makePanel({
            sync: async (_sourceSlug, request) => {
                syncCalls += 1;
                await new Promise<void>((resolve) => {
                    resolveSync = resolve;
                });
                return { queued: true, corpId: request.corpId };
            },
        });
        Object.assign(panel, { loaded: true });
        await settled(panel);

        const input = panel.shadowRoot?.querySelector<HTMLInputElement>(
            "#dingtalk-directory-corp-id",
        );
        const form = panel.shadowRoot?.querySelector("form");
        const button = panel.shadowRoot?.querySelector<HTMLButtonElement>('button[type="submit"]');
        input!.value = "corp-a";
        input!.dispatchEvent(new InputEvent("input", { bubbles: true }));

        form!.dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }));
        button!.click();
        await settled(panel);

        expect(syncCalls).toBe(1);
        expect(button?.disabled).toBe(true);

        resolveSync?.();
        await settled(panel);

        expect(syncCalls).toBe(1);
    });

    it("routes empty corp submission to field error and toast", async () => {
        const panel = makePanel({});
        Object.assign(panel, { loaded: true });
        await settled(panel);

        panel
            .shadowRoot!.querySelector("form")!
            .dispatchEvent(new SubmitEvent("submit", { bubbles: true, cancelable: true }));
        await settled(panel);

        const input = panel.shadowRoot?.querySelector<HTMLInputElement>(
            "#dingtalk-directory-corp-id",
        );
        expect(input?.getAttribute("aria-invalid")).toBe("true");
        expect(panel.shadowRoot?.textContent).toContain(
            "Corp ID is required to queue a DingTalk directory sync.",
        );
        expect(vi.mocked(showMessage).mock.calls.at(-1)?.[0].level).toBe(MessageLevel.error);
    });

    it("disables delete for running rows and gives terminal rows unique names", async () => {
        const panel = makePanel({
            status: async () => ({
                sourceSlug: "source-a",
                canChange: true,
                sync: [makeSyncStatus("corp-a", "running"), makeSyncStatus("corp-b", "success")],
            }),
        });
        await settled(panel);

        const disabledDelete = panel.shadowRoot?.querySelector<HTMLButtonElement>(
            'button[aria-label*="corp-a"]',
        );
        const enabledDelete = panel.shadowRoot?.querySelector<HTMLButtonElement>(
            'button[aria-label*="corp-b"]',
        );

        expect(disabledDelete?.disabled).toBe(true);
        expect(enabledDelete?.disabled).toBe(false);
        expect(panel.shadowRoot?.querySelector("table")?.getAttribute("role")).toBeNull();
        expect(panel.shadowRoot?.querySelector("caption")?.textContent).toContain(
            "DingTalk directory sync status by corp",
        );
    });

    it("announces terminal refresh outcomes once", async () => {
        const panel = makePanel({
            status: async () => ({
                sourceSlug: "source-a",
                canChange: true,
                sync: [
                    {
                        ...makeSyncStatus("corp-a", "success", {
                            counters: { warnings: ["partial detail failure"] },
                        }),
                        generation: 1,
                    },
                    {
                        ...makeSyncStatus("corp-b", "error", { error: "provider denied" }),
                        generation: 1,
                    },
                ],
            }),
        });
        await settled(panel);
        vi.mocked(showMessage).mockClear();

        await panel.shadowRoot?.querySelector("ak-spinner-button")?.callAction?.();
        await panel.shadowRoot?.querySelector("ak-spinner-button")?.callAction?.();

        expect(vi.mocked(showMessage).mock.calls).toHaveLength(2);
        expect(vi.mocked(showMessage).mock.calls[0]?.[0].level).toBe(MessageLevel.warning);
        expect(vi.mocked(showMessage).mock.calls[1]?.[0].level).toBe(MessageLevel.error);
    });

    it("keeps directory status visible but disables sync and delete when canChange is false", async () => {
        const panel = makePanel({
            status: async () => ({
                sourceSlug: "source-a",
                canChange: false,
                sync: [makeSyncStatus("corp-a", "success")],
            }),
        });
        await settled(panel);

        expect(panel.shadowRoot?.textContent).toContain(
            "You can view DingTalk directory status, but you need permission",
        );
        expect(
            panel.shadowRoot?.querySelector<HTMLInputElement>("#dingtalk-directory-corp-id")
                ?.disabled,
        ).toBe(true);
        expect(
            panel.shadowRoot?.querySelector<HTMLButtonElement>('button[type="submit"]')?.disabled,
        ).toBe(true);
        expect(
            panel.shadowRoot?.querySelector<HTMLButtonElement>('button[aria-label*="corp-a"]')
                ?.disabled,
        ).toBe(true);
        expect(panel.shadowRoot?.textContent).toContain("corp-a");
    });
});
