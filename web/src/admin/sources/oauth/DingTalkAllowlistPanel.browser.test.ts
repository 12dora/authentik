import type { DingTalkAllowlistPanel } from "#admin/sources/oauth/DingTalkAllowlistPanel";
import type {
    confirmDingTalkDestructiveAction as ConfirmDingTalkDestructiveAction,
    DingTalkDestructiveActionModal,
} from "#admin/sources/oauth/DingTalkDestructiveActionModal";

import type { OAuthSource } from "@goauthentik/api";

import { userEvent } from "@vitest/browser/context";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { html } from "lit";

vi.mock("#elements/messages/MessageContainer", () => ({
    showMessage: vi.fn(),
}));

vi.mock("#admin/policies/expression/ExpressionPolicyForm", () => ({
    ExpressionPolicyForm: class ExpressionPolicyForm extends HTMLElement {},
}));

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
    GroupMatchingModeEnum: {
        Identifier: "identifier",
        Name: "name",
    },
    SourcesApi: class SourcesApi {},
    UiThemeEnum: {
        Automatic: "automatic",
        Dark: "dark",
        Light: "light",
    },
    UserMatchingModeEnum: {
        Email: "email",
        Identifier: "identifier",
        Username: "username",
    },
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

vi.mock("#common/sentry/index", () => ({
    SentryIgnoredError: class SentryIgnoredError extends Error {},
}));

vi.mock("#elements/entities/used-by", () => ({
    usedByLabel: () => "",
}));

interface AllowlistPanelHarness {
    loaded: boolean;
    model: {
        companies: Array<{ corpId: string; label: string; allowAll: boolean; deptIds: string[] }>;
    };
    status?: {
        revision: string;
        canManage: boolean;
        managedPolicy: { _exists: boolean };
        policyBindings: Array<{ _exists: boolean; enabled: boolean; pk: string; target?: string }>;
    };
    allowlistApi: {
        sourcesOauthDingtalkAllowlistApplyCreate: ReturnType<typeof vi.fn>;
    };
    refreshStatus: ReturnType<typeof vi.fn>;
}

let DingTalkAllowlistPanelElement: typeof DingTalkAllowlistPanel;
let confirmDingTalkDestructiveAction: typeof ConfirmDingTalkDestructiveAction;

function setAuthentikGlobal(): void {
    if (!document.getElementById("interface-root")) {
        const interfaceRoot = document.createElement("main");
        interfaceRoot.id = "interface-root";
        Object.defineProperty(interfaceRoot, "renderRoot", {
            value: interfaceRoot,
            configurable: true,
        });
        document.body.append(interfaceRoot);
    }
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
            capabilities: [],
        },
        locale: "en",
        versionFamily: "",
        versionSubdomain: "",
        build: "",
    };
}

function makeSource(slug: string): OAuthSource {
    return {
        slug,
        enabled: true,
        authenticationFlow: "auth-flow",
        enrollmentFlow: "enrollment-flow",
    } as OAuthSource;
}

function mountPanel(): DingTalkAllowlistPanel & AllowlistPanelHarness {
    const panel = new DingTalkAllowlistPanelElement();
    document.body.append(panel);
    return panel as unknown as DingTalkAllowlistPanel & AllowlistPanelHarness;
}

function activeDialogModal(): DingTalkDestructiveActionModal {
    const modal = document.querySelector(
        "ak-source-oauth-dingtalk-destructive-action",
    ) as DingTalkDestructiveActionModal | null;
    expect(modal).toBeTruthy();
    return modal as DingTalkDestructiveActionModal;
}

function updateInput(input: HTMLInputElement, value: string): void {
    input.value = value;
    input.dispatchEvent(new InputEvent("input", { bubbles: true, composed: true }));
}

function findButton(root: ShadowRoot, label: string): HTMLButtonElement {
    const button = Array.from(root.querySelectorAll("button")).find((button) =>
        button.textContent?.includes(label),
    );
    expect(button).toBeTruthy();
    return button as HTMLButtonElement;
}

async function renderLoadedPanel(
    panel: DingTalkAllowlistPanel & AllowlistPanelHarness,
    canManage = true,
): Promise<void> {
    await panel.updateComplete;
    panel.status = {
        revision: "rev-1",
        canManage,
        managedPolicy: { _exists: false },
        policyBindings: [],
    };
    panel.loaded = true;
    await panel.updateComplete;
}

describe("DingTalkAllowlistPanel", () => {
    beforeEach(async () => {
        ({ DingTalkAllowlistPanel: DingTalkAllowlistPanelElement } =
            await import("#admin/sources/oauth/DingTalkAllowlistPanel"));
        ({ confirmDingTalkDestructiveAction } =
            await import("#admin/sources/oauth/DingTalkDestructiveActionModal"));
    });

    afterEach(() => {
        document
            .querySelectorAll(
                "ak-source-oauth-dingtalk-allowlist, ak-source-oauth-dingtalk-destructive-action, dialog",
            )
            .forEach((element) => element.remove());
        vi.restoreAllMocks();
    });

    it("adds a manual company in restricted mode", async () => {
        setAuthentikGlobal();
        const panel = mountPanel();
        panel.refreshStatus = vi.fn(async () => {});
        panel.source = makeSource("dingtalk");
        await renderLoadedPanel(panel);

        const corpIdInput = panel.shadowRoot?.querySelector<HTMLInputElement>(
            "#dingtalk-manual-corp-id",
        );
        const labelInput =
            panel.shadowRoot?.querySelector<HTMLInputElement>("#dingtalk-manual-label");

        expect(corpIdInput).toBeTruthy();
        expect(labelInput).toBeTruthy();

        updateInput(corpIdInput as HTMLInputElement, " corp-a ");
        updateInput(labelInput as HTMLInputElement, " Alpha ");
        findButton(panel.shadowRoot as ShadowRoot, "Add company").click();

        await panel.updateComplete;

        expect(panel.model).toEqual({
            companies: [
                {
                    corpId: "corp-a",
                    label: "Alpha",
                    allowAll: false,
                    deptIds: [],
                },
            ],
        });
    });

    it("renders allowlist controls read-only when canManage is false", async () => {
        setAuthentikGlobal();
        const panel = mountPanel();
        panel.refreshStatus = vi.fn(async () => {});
        panel.source = makeSource("dingtalk");
        await renderLoadedPanel(panel, false);

        expect(panel.shadowRoot?.textContent).toContain(
            "You can view this DingTalk allowlist, but you need permission",
        );
        expect(
            panel.shadowRoot?.querySelector<HTMLInputElement>("#dingtalk-manual-corp-id")?.disabled,
        ).toBe(true);
        expect(findButton(panel.shadowRoot as ShadowRoot, "Add company").disabled).toBe(true);
        const saveButton = panel.shadowRoot?.querySelector(
            "ak-spinner-button.pf-m-primary",
        ) as HTMLElement & { disabled?: boolean };
        expect(saveButton?.disabled).toBe(true);
    });

    it("sends the current revision to the backend allowlist apply transaction", async () => {
        setAuthentikGlobal();
        const panel = mountPanel();
        const status = {
            revision: "rev-2",
            canManage: true,
            managedPolicy: { _exists: true },
            sourceLinkGuard: { enabled: true },
            config: {
                companies: [
                    {
                        corp_id: "corp-a",
                        label: "Alpha",
                        allow_all: false,
                        dept_ids: ["10"],
                    },
                ],
            },
            policyBindings: [{ _exists: true, enabled: true, pk: "binding-pk" }],
        };
        const apply = vi.fn(async () => status);
        panel.refreshStatus = vi.fn(async () => {});
        panel.source = makeSource("dingtalk");
        panel.allowlistApi = {
            sourcesOauthDingtalkAllowlistApplyCreate: apply,
        };
        await renderLoadedPanel(panel);
        panel.status = { ...status, revision: "rev-1" };
        await panel.updateComplete;

        const corpIdInput = panel.shadowRoot?.querySelector<HTMLInputElement>(
            "#dingtalk-manual-corp-id",
        );
        const labelInput =
            panel.shadowRoot?.querySelector<HTMLInputElement>("#dingtalk-manual-label");
        expect(corpIdInput).toBeTruthy();
        expect(labelInput).toBeTruthy();

        updateInput(corpIdInput as HTMLInputElement, "corp-a");
        updateInput(labelInput as HTMLInputElement, "Alpha");
        findButton(panel.shadowRoot as ShadowRoot, "Add company").click();
        await panel.updateComplete;

        const departmentInput = panel.shadowRoot?.querySelector<HTMLInputElement>(
            ".ak-dingtalk-department-input",
        );
        expect(departmentInput).toBeTruthy();
        updateInput(departmentInput as HTMLInputElement, "10");
        findButton(panel.shadowRoot as ShadowRoot, "Add departments").click();
        await panel.updateComplete;

        const saveButton = panel.shadowRoot?.querySelector(
            "ak-spinner-button.pf-m-primary",
        ) as HTMLElement | null;
        expect(saveButton).toBeTruthy();

        await (saveButton as HTMLElement & { updateComplete: Promise<unknown> }).updateComplete;
        const innerButton = saveButton?.shadowRoot?.querySelector<HTMLButtonElement>("button");
        expect(innerButton).toBeTruthy();

        innerButton?.click();

        await vi.waitFor(() => {
            expect(apply).toHaveBeenCalledWith("dingtalk", {
                config: panel.model,
                expectedRevision: "rev-1",
            });
        });
        expect(panel.status.revision).toBe("rev-2");
    });
});

describe("DingTalkDestructiveActionModal", () => {
    afterEach(() => {
        document
            .querySelectorAll("ak-source-oauth-dingtalk-destructive-action, dialog, button")
            .forEach((element) => element.remove());
        vi.restoreAllMocks();
    });

    it("opens with an accessible name and initial focus on the cancel button", async () => {
        setAuthentikGlobal();
        const trigger = document.createElement("button");
        trigger.textContent = "Open delete";
        document.body.append(trigger);

        const done = confirmDingTalkDestructiveAction(
            {
                headline: "Delete DingTalk directory data for corp-a",
                body: html`<p>Confirm delete.</p>`,
                action: "Delete",
                successMessage: "Deleted",
                errorMessage: "Failed",
                onConfirm: vi.fn(async () => undefined),
            },
            trigger,
        );
        await new Promise((resolve) => requestAnimationFrame(resolve));
        const modal = activeDialogModal();
        await modal.updateComplete;
        await new Promise((resolve) => requestAnimationFrame(resolve));

        expect(modal.formatARIALabel()).toBe("Delete DingTalk directory data for corp-a");
        expect(modal.shadowRoot?.activeElement?.textContent?.trim()).toBe("Cancel");

        modal.close();
        await done;
    });

    it("keeps tab focus in the native modal and leaves the background inert", async () => {
        setAuthentikGlobal();
        const background = document.createElement("button");
        background.textContent = "Background action";
        document.body.append(background);
        const trigger = document.createElement("button");
        trigger.textContent = "Remove allowlist";
        document.body.append(trigger);
        trigger.focus();

        const done = confirmDingTalkDestructiveAction(
            {
                headline: "Remove DingTalk allowlist",
                body: html`<p>Confirm remove.</p>`,
                action: "Remove allowlist",
                successMessage: "Removed",
                errorMessage: "Failed",
                onConfirm: vi.fn(async () => undefined),
            },
            trigger,
        );
        await new Promise((resolve) => requestAnimationFrame(resolve));
        const modal = activeDialogModal();
        await modal.updateComplete;
        await new Promise((resolve) => requestAnimationFrame(resolve));
        const dialog = modal.parentElement as HTMLDialogElement;

        expect(dialog.open).toBe(true);
        await userEvent.keyboard("{Tab}");

        expect(document.activeElement).not.toBe(background);
        expect(dialog.open).toBe(true);

        await userEvent.keyboard("{Escape}");
        await done;

        expect(document.querySelector("ak-source-oauth-dingtalk-destructive-action")).toBeNull();
        expect(document.activeElement).toBe(trigger);
    });
});
