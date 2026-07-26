import "#components/ak-status-label";
import "#elements/buttons/SpinnerButton/index";
import "#elements/EmptyState";
import "#elements/Alert";

import { DingTalkAllowlistApi, type DingTalkAllowlistStatus } from "./DingTalkAllowlistApi";

import { DEFAULT_CONFIG } from "#common/api/config";
import { parseAPIResponseError, pluckErrorDetail } from "#common/errors/network";
import { MessageLevel } from "#common/messages";

import { AKElement } from "#elements/Base";
import { modalInvoker, renderDialog } from "#elements/dialogs";
import { showMessage } from "#elements/messages/MessageContainer";
import { SlottedTemplateResult } from "#elements/types";

import { ExpressionPolicyForm } from "#admin/policies/expression/ExpressionPolicyForm";
import type { StatusItem } from "#admin/sources/oauth/DingTalkAllowlistPanelState";
import {
    addDingTalkDepartments,
    applyDingTalkDepartmentInputs,
    dingtalkDepartmentFetchFailureStatus,
    dingtalkDepartmentInputsFromModel,
    dingtalkStatusLabelProperties,
    isDingTalkCompanyMissingDepartments,
    removeDingTalkCompany,
    renderDingTalkDepartmentInput,
    saveDingTalkAllowlistConfiguration,
    updateDingTalkCompany,
    upsertDingTalkCompany,
    validatedDingTalkDiscoveryUrl,
} from "#admin/sources/oauth/DingTalkAllowlistPanelState";
import {
    DingTalkAllowlistModel,
    dingTalkAllowlistModelFromStoredConfig,
} from "#admin/sources/oauth/DingTalkAllowlistPolicy";
import { DingTalkDepartmentPickerModal } from "#admin/sources/oauth/DingTalkDepartmentPickerModal";
import { confirmDingTalkDestructiveAction } from "#admin/sources/oauth/DingTalkDestructiveActionModal";

import { type OAuthSource, SourcesApi } from "@goauthentik/api";

import { msg, str } from "@lit/localize";
import { css, CSSResult, html, nothing, PropertyValues, TemplateResult } from "lit";
import { customElement, property, state } from "lit/decorators.js";
import { ifDefined } from "lit/directives/if-defined.js";

import PFButton from "@patternfly/patternfly/components/Button/button.css";
import PFCard from "@patternfly/patternfly/components/Card/card.css";
import PFContent from "@patternfly/patternfly/components/Content/content.css";
import PFForm from "@patternfly/patternfly/components/Form/form.css";
import PFFormControl from "@patternfly/patternfly/components/FormControl/form-control.css";
import PFSwitch from "@patternfly/patternfly/components/Switch/switch.css";
import PFTableGrid from "@patternfly/patternfly/components/Table/table-grid.css";
import PFTable from "@patternfly/patternfly/components/Table/table.css";
import PFFlex from "@patternfly/patternfly/layouts/Flex/flex.css";

const DINGTALK_DISCOVERY_MESSAGE_SOURCE = "goauthentik.io";
const DINGTALK_DISCOVERY_MESSAGE_CONTEXT = "dingtalk-allowlist-discovery";

// How often the discovery popup is polled for a manual close so the pending action
// resolves instead of leaving the discovery button spinning forever.
const DINGTALK_DISCOVERY_POLL_INTERVAL = 500;

interface DingTalkDiscoveryResult {
    corpId: string;
    label?: string;
    userId?: string;
}

type DingTalkAllowlistErrorCode =
    | "authorization_code_missing"
    | "department_access_denied"
    | "department_dependency_unavailable"
    | "department_response_invalid"
    | "provider_response_invalid"
    | "provider_unavailable"
    | "state_expired"
    | "state_invalid"
    | "state_replayed"
    | "state_source_mismatch";

const DINGTALK_ALLOWLIST_ERROR_CODES = new Set<DingTalkAllowlistErrorCode>([
    "authorization_code_missing",
    "department_access_denied",
    "department_dependency_unavailable",
    "department_response_invalid",
    "provider_response_invalid",
    "provider_unavailable",
    "state_expired",
    "state_invalid",
    "state_replayed",
    "state_source_mismatch",
]);

interface DingTalkDepartment {
    deptId: string;
    name: string;
    parentId: string | null;
}

function normalizeOptionalString(value: unknown): string {
    if (value === undefined || value === null) {
        return "";
    }
    return String(value).trim();
}

function isDingTalkAllowlistErrorCode(value: string): value is DingTalkAllowlistErrorCode {
    return DINGTALK_ALLOWLIST_ERROR_CODES.has(value as DingTalkAllowlistErrorCode);
}

function normalizeDingTalkAllowlistErrorCode(value: unknown): DingTalkAllowlistErrorCode | null {
    const code = normalizeOptionalString(value);
    return isDingTalkAllowlistErrorCode(code) ? code : null;
}

function extractDingTalkAllowlistErrorCode(value: unknown): DingTalkAllowlistErrorCode | null {
    const direct = normalizeDingTalkAllowlistErrorCode(value);
    if (direct) {
        return direct;
    }
    if (Array.isArray(value)) {
        for (const item of value) {
            const code = extractDingTalkAllowlistErrorCode(item);
            if (code) {
                return code;
            }
        }
        return null;
    }
    if (!value || typeof value !== "object") {
        return null;
    }
    const record = value as Record<string, unknown>;
    for (const key of ["error_code", "errorCode", "code", "non_field_errors"]) {
        const code = extractDingTalkAllowlistErrorCode(record[key]);
        if (code) {
            return code;
        }
    }
    const detail = record.detail;
    if (detail && typeof detail === "object") {
        return extractDingTalkAllowlistErrorCode(detail);
    }
    return null;
}

// The departments field of the API response is an untyped JSON field; the entries
// use the backend's snake_case keys.
function normalizeDingTalkDepartments(value: unknown): DingTalkDepartment[] {
    if (!Array.isArray(value)) {
        return [];
    }
    return value.flatMap((item): DingTalkDepartment[] => {
        if (!item || typeof item !== "object") {
            return [];
        }
        const record = item as Record<string, unknown>;
        const deptId = normalizeOptionalString(record.dept_id ?? record.deptId);
        if (!deptId) {
            return [];
        }
        return [
            {
                deptId,
                name: normalizeOptionalString(record.name) || deptId,
                parentId: normalizeOptionalString(record.parent_id ?? record.parentId) || null,
            },
        ];
    });
}

function initialLastDepartmentFetchStatus(): StatusItem {
    return {
        label: msg("Last department fetch", {
            id: "sources.oauth.dingtalk-allowlist.status.departments.label",
        }),
        state: "unknown",
        detail: msg("Not run", { id: "sources.oauth.dingtalk-allowlist.status.not-run" }),
    };
}

// Immutably drops a key from a per-corp state record so removing a company also
// discards its cached inputs/departments instead of leaking them onto a later re-add.
function omitRecordKey<T>(record: Record<string, T>, key: string): Record<string, T> {
    if (!(key in record)) {
        return record;
    }
    const next = { ...record };
    delete next[key];
    return next;
}

@customElement("ak-source-oauth-dingtalk-allowlist")
export class DingTalkAllowlistPanel extends AKElement {
    @property({ attribute: false })
    public source?: OAuthSource;

    @state()
    private model: DingTalkAllowlistModel = { companies: [] };

    @state()
    private status?: DingTalkAllowlistStatus;

    @state()
    private manualCorpId = "";

    @state()
    private manualLabel = "";

    @state()
    private departmentInputs: Record<string, string> = {};

    // Companies detected on a shared flow that belong to a sibling DingTalk source.
    // Kept read-only until the admin explicitly adopts them, so this source never
    // silently shows or saves another source's allowlist.
    @state()
    private detectedSharedConfig?: DingTalkAllowlistModel;

    @state()
    private lastDiscovery?: DingTalkDiscoveryResult;

    @state()
    private lastDepartmentFetch: StatusItem = initialLastDepartmentFetchStatus();

    @state()
    private sourceLinkGuard: StatusItem = {
        label: msg("Source-link guard", {
            id: "sources.oauth.dingtalk-allowlist.status.source-link.label",
        }),
        state: "unknown",
        detail: msg("Unknown", { id: "sources.oauth.dingtalk-allowlist.status.unknown" }),
    };

    @state()
    private expressionValid: boolean | null | undefined;

    @state()
    private partialFailures: string[] = [];

    // Cleared to true once the first status refresh settles; until then the panel
    // renders a loading state instead of half-populated "misconfigured" statuses.
    @state()
    private loaded = false;

    // Set once the admin edits the local allowlist; while set, status refreshes keep
    // the local model and inputs instead of overwriting them with server state.
    // Reactive so an unsaved-changes banner can surface it.
    @state()
    private dirty = false;

    // Incremented per refresh; stale refreshes must not overwrite newer state.
    private refreshGeneration = 0;

    private discoveryPopup: Window | null = null;

    // Interval id polling the discovery popup for a manual close, plus the resolver
    // that keeps the discovery action pending until a result arrives or it closes.
    private discoveryPoll?: ReturnType<typeof setInterval>;
    private discoveryDone?: () => void;

    // True while a text input has an active IME composition; suppresses the state
    // rewrite (and thus the `.value` write-back) that would break Chinese input.
    private composing = false;

    private sourcesApi = new SourcesApi(DEFAULT_CONFIG);
    private allowlistApi = new DingTalkAllowlistApi(DEFAULT_CONFIG);

    static styles: CSSResult[] = [
        PFButton,
        PFCard,
        PFContent,
        PFForm,
        PFFormControl,
        PFSwitch,
        PFTable,
        PFTableGrid,
        PFFlex,
        css`
            .ak-dingtalk-section {
                margin-block-end: var(--pf-global--spacer--lg);
            }

            .ak-dingtalk-actions {
                gap: var(--pf-global--spacer--sm);
            }

            .ak-dingtalk-inline-form {
                align-items: flex-end;
                gap: var(--pf-global--spacer--md);
            }

            .ak-dingtalk-inline-form .pf-c-form__group {
                min-width: 14rem;
            }

            .ak-dingtalk-input-row {
                display: flex;
                gap: var(--pf-global--spacer--sm);
                flex-wrap: wrap;
                align-items: center;
            }

            .ak-dingtalk-input-row .pf-c-form-control {
                min-width: 12rem;
            }

            .ak-dingtalk-department-input {
                min-width: 18rem;
            }

            .ak-dingtalk-status {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr));
                gap: var(--pf-global--spacer--sm);
                margin: 0;
                padding: 0;
                list-style: none;
            }

            .ak-dingtalk-status li {
                display: flex;
                gap: var(--pf-global--spacer--sm);
                align-items: baseline;
            }

            .ak-dingtalk-muted {
                color: var(--pf-global--Color--200);
            }

            .ak-dingtalk-table-actions {
                display: flex;
                gap: var(--pf-global--spacer--sm);
                flex-wrap: wrap;
            }
        `,
    ];

    connectedCallback(): void {
        super.connectedCallback();
        window.addEventListener("message", this.handleDiscoveryMessage);
    }

    disconnectedCallback(): void {
        window.removeEventListener("message", this.handleDiscoveryMessage);
        this.finishDiscovery();
        super.disconnectedCallback();
    }

    protected willUpdate(changedProperties: PropertyValues<this>): void {
        if (changedProperties.has("source")) {
            // Only a different source warrants an automatic refresh; the same source
            // object is re-assigned after saves and global refresh events, and
            // refreshing again would race the explicit refresh already in flight.
            const previous = changedProperties.get("source") as OAuthSource | undefined;
            if (previous?.slug !== this.source?.slug) {
                this.resetSourceState();
            }
            if (this.source?.slug && previous?.slug !== this.source.slug) {
                this.refreshStatus().catch(console.error);
            }
        }
    }

    private resetSourceState(): void {
        // Invalidate every in-flight read before clearing the source-scoped draft. This
        // prevents a late response for source A from repopulating the panel for source B.
        this.refreshGeneration += 1;
        this.finishDiscovery();
        this.model = { companies: [] };
        this.status = undefined;
        this.manualCorpId = "";
        this.manualLabel = "";
        this.departmentInputs = {};
        this.detectedSharedConfig = undefined;
        this.lastDiscovery = undefined;
        this.lastDepartmentFetch = initialLastDepartmentFetchStatus();
        this.expressionValid = undefined;
        this.partialFailures = [];
        this.loaded = false;
        this.dirty = false;
    }

    private markDirty(): void {
        this.dirty = true;
    }

    private get canManage(): boolean {
        return this.status?.canManage === true;
    }

    private get readOnlyNotice(): TemplateResult {
        return html`<ak-alert class="ak-dingtalk-section" level="info" icon="fa-lock">
            ${msg(
                "You can view this DingTalk allowlist, but you need permission to change this OAuth source before editing or applying changes.",
                {
                    id: "sources.oauth.dingtalk-allowlist.read-only",
                },
            )}
        </ak-alert>`;
    }

    // IME-safe input plumbing: while a composition is active we must not push the
    // intermediate value back into component state, because re-rendering rewrites the
    // input's `.value` and cancels the in-progress Chinese composition.
    private startComposition = (): void => {
        this.composing = true;
    };

    private handleComposedInput(event: Event, commit: (value: string) => void): void {
        if (this.composing) {
            return;
        }
        commit((event.target as HTMLInputElement).value);
    }

    private handleCompositionEnd(event: CompositionEvent, commit: (value: string) => void): void {
        this.composing = false;
        commit((event.target as HTMLInputElement).value);
    }

    // Lets a bare input submit its adjacent action on Enter. Ignored mid-IME-composition
    // (event.isComposing / our own flag) so committing a Chinese candidate with Enter does
    // not also fire the action.
    private handleSubmitKey(event: KeyboardEvent, submit: () => void): void {
        if (event.key !== "Enter" || event.isComposing || this.composing) {
            return;
        }
        event.preventDefault();
        submit();
    }

    private handleDiscoveryMessage = (event: MessageEvent<unknown>): void => {
        if (event.origin !== window.location.origin) {
            return;
        }
        if (!this.discoveryPopup || event.source !== this.discoveryPopup) {
            return;
        }
        if (!event.data || typeof event.data !== "object") {
            return;
        }
        const record = event.data as Record<string, unknown>;
        if (
            record.source !== DINGTALK_DISCOVERY_MESSAGE_SOURCE ||
            record.context !== DINGTALK_DISCOVERY_MESSAGE_CONTEXT
        ) {
            return;
        }
        this.finishDiscovery();
        if (record.ok === false) {
            showMessage({
                level: MessageLevel.error,
                message: this.localizeDingTalkDiscoveryError(
                    extractDingTalkAllowlistErrorCode(record),
                ),
            });
            return;
        }
        if (!this.canManage) {
            showMessage({
                level: MessageLevel.warning,
                message: msg("You do not have permission to change this DingTalk allowlist.", {
                    id: "sources.oauth.dingtalk-allowlist.read-only.toast",
                }),
            });
            return;
        }
        const result = this.extractDiscoveryResult(record);
        if (!result) {
            showMessage({
                level: MessageLevel.error,
                message: msg("DingTalk discovery did not return a company ID.", {
                    id: "sources.oauth.dingtalk-allowlist.discovery.missing-corp-id",
                }),
            });
            return;
        }
        this.lastDiscovery = result;
        // A freshly discovered company defaults to restricted (no departments) so an
        // inattentive save cannot grant org-wide access; the admin explicitly opts
        // into full-company access or picks the allowed departments.
        this.upsertCompany(result.corpId, result.label || result.corpId, false, []);
        showMessage({
            level: MessageLevel.info,
            message: msg(
                str`Discovered DingTalk company ${result.corpId}. Select the departments allowed to sign in, or enable full-company access.`,
                {
                    id: "sources.oauth.dingtalk-allowlist.discovery.success",
                },
            ),
        });
    };

    // Clears the discovery popup reference and its close-poll, and resolves the
    // pending discovery action (if any). Idempotent; safe to call more than once.
    private finishDiscovery(): void {
        // Close the popup ourselves if it is still open — the discovery page posts its
        // result but may not self-close. Do this before dropping the reference, after
        // which the close-poll can no longer reach it. close() on an already-closed
        // window is a no-op.
        this.discoveryPopup?.close();
        this.discoveryPopup = null;
        if (this.discoveryPoll !== undefined) {
            clearInterval(this.discoveryPoll);
            this.discoveryPoll = undefined;
        }
        const done = this.discoveryDone;
        this.discoveryDone = undefined;
        done?.();
    }

    private extractDiscoveryResult(
        record: Record<string, unknown>,
    ): DingTalkDiscoveryResult | null {
        const profile =
            record.profile && typeof record.profile === "object"
                ? (record.profile as Record<string, unknown>)
                : {};
        // New callbacks return a minimal canonical DTO at the top level. Older
        // callbacks carried full profile data; keep it as a fallback only.
        const payload = { ...profile, ...record };
        const corpId = normalizeOptionalString(payload.corpId ?? payload.corp_id);
        if (!corpId) {
            return null;
        }
        return {
            corpId,
            label: normalizeOptionalString(
                payload.label ??
                    payload.company ??
                    payload.company_name ??
                    payload.companyName ??
                    payload.corp_name ??
                    payload.corpName,
            ),
            userId: normalizeOptionalString(payload.userId ?? payload.userid ?? payload.user_id),
        };
    }

    private async refreshStatus(): Promise<void> {
        if (!this.source?.slug) {
            return;
        }
        const sourceSlug = this.source.slug;
        const generation = ++this.refreshGeneration;
        const failures: string[] = [];

        try {
            const status = await this.allowlistApi.sourcesOauthDingtalkAllowlistStatusRetrieve({
                sourceSlug,
            });
            if (generation === this.refreshGeneration && this.source?.slug === sourceSlug) {
                this.applyBackendStatus(status);
            }
        } catch (error) {
            if (generation === this.refreshGeneration && this.source?.slug === sourceSlug) {
                this.status = undefined;
                this.expressionValid = undefined;
                this.sourceLinkGuard = {
                    label: this.sourceLinkGuard.label,
                    state: "unknown",
                    detail: msg("Discovery status endpoint unavailable", {
                        id: "sources.oauth.dingtalk-allowlist.status.endpoint-unavailable",
                    }),
                };
            }
            failures.push(await this.apiErrorMessage(error));
        }

        if (generation === this.refreshGeneration && this.source?.slug === sourceSlug) {
            this.partialFailures = failures;
            // The first refresh for this source has settled; render real status.
            this.loaded = true;
        }
    }

    // Refresh action bound to the "Refresh status" button. Unlike refreshStatus it
    // rejects when any sub-call failed so the spinner button shows a failure state
    // instead of a success animation that hides the errors.
    private async refreshStatusAction(): Promise<void> {
        await this.refreshStatus();
        if (this.partialFailures.length > 0) {
            throw new Error(
                msg("Some DingTalk status checks failed. See the details below.", {
                    id: "sources.oauth.dingtalk-allowlist.status.refresh-failed",
                }),
            );
        }
    }

    private applyBackendStatus(status: DingTalkAllowlistStatus): void {
        this.status = status;
        this.expressionValid = status.managedPolicy._exists ? true : undefined;
        // The status config is discovered by walking every binding on the shared
        // flows and can belong to another DingTalk source. It must NOT prefill this
        // source's editable model: doing so would silently show (and, on save, adopt)
        // a sibling's allowlist. Instead we surface it read-only as "detected on a
        // shared flow" and require the admin to explicitly adopt it before it becomes
        // editable and saveable here.
        const config = status.config as { companies?: unknown } | null;
        if (
            !status.managedPolicy._exists &&
            !this.dirty &&
            config &&
            Array.isArray(config.companies) &&
            config.companies.length > 0
        ) {
            this.detectedSharedConfig = dingTalkAllowlistModelFromStoredConfig(config) ?? undefined;
        } else {
            this.detectedSharedConfig = undefined;
        }
        if (status.managedPolicy._exists && !this.dirty) {
            const parsed = dingTalkAllowlistModelFromStoredConfig(status.config);
            this.model = parsed ?? { companies: [] };
            this.departmentInputs = parsed ? dingtalkDepartmentInputsFromModel(parsed) : {};
        }
        if (!status.managedPolicy._exists && !this.dirty) {
            this.model = { companies: [] };
            this.departmentInputs = {};
        }

        const enabled = status.sourceLinkGuard?.enabled ?? false;
        this.sourceLinkGuard = {
            label: this.sourceLinkGuard.label,
            state: enabled ? "good" : "danger",
            goodLabel: msg("OK", {
                id: "sources.oauth.dingtalk-allowlist.status.good.ok",
            }),
            detail: enabled
                ? msg("Installed or disabled", {
                      id: "sources.oauth.dingtalk-allowlist.status.source-link.good",
                  })
                : msg("Needs review", {
                      id: "sources.oauth.dingtalk-allowlist.status.source-link.review",
                  }),
        };
    }

    // Explicit opt-in for the companies detected on a shared flow: copy them into the
    // editable model, mark the panel dirty, and drop the read-only banner.
    private adoptDetectedSharedConfig(): void {
        if (!this.canManage) {
            return;
        }
        const detected = this.detectedSharedConfig;
        if (!detected) {
            return;
        }
        this.model = detected;
        this.departmentInputs = dingtalkDepartmentInputsFromModel(detected);
        this.detectedSharedConfig = undefined;
        this.markDirty();
    }

    private upsertCompany(
        corpId: string,
        label: string,
        allowAll: boolean,
        deptIds: string[],
    ): void {
        if (!this.canManage) {
            return;
        }
        const existing = this.model.companies.some((company) => company.corpId === corpId);
        this.model = upsertDingTalkCompany(this.model, corpId, label, allowAll, deptIds);
        if (!existing) {
            this.departmentInputs = {
                ...this.departmentInputs,
                [corpId]: allowAll ? "" : renderDingTalkDepartmentInput(deptIds),
            };
        }
        this.markDirty();
    }

    private addManualCompany(): void {
        const corpId = normalizeOptionalString(this.manualCorpId);
        if (!corpId) {
            showMessage({
                level: MessageLevel.error,
                message: msg("Company corpId is required.", {
                    id: "sources.oauth.dingtalk-allowlist.validation.corp-id-required",
                }),
            });
            return;
        }
        this.upsertCompany(corpId, normalizeOptionalString(this.manualLabel) || corpId, false, []);
        this.manualCorpId = "";
        this.manualLabel = "";
    }

    private updateCompany(corpId: string, patch: Partial<DingTalkAllowlistModel["companies"][0]>) {
        if (!this.canManage) {
            return;
        }
        this.model = updateDingTalkCompany(this.model, corpId, patch);
        this.markDirty();
    }

    private removeCompany(corpId: string): void {
        if (!this.canManage) {
            return;
        }
        this.model = removeDingTalkCompany(this.model, corpId);
        // Drop the per-corp UI state too, otherwise re-adding the same corpId would
        // surface the previous round's (possibly stale) department input.
        this.departmentInputs = omitRecordKey(this.departmentInputs, corpId);
        this.markDirty();
    }

    private addDepartments(corpId: string): void {
        if (!this.canManage) {
            return;
        }
        const result = addDingTalkDepartments(this.model, this.departmentInputs, corpId);
        if (result.error) {
            showMessage({
                level: MessageLevel.error,
                message: result.invalidDepartmentId
                    ? msg(
                          str`${result.invalidDepartmentId} is not a valid DingTalk department ID.`,
                          {
                              id: "sources.oauth.dingtalk-allowlist.validation.department-id-invalid",
                          },
                      )
                    : result.error,
            });
            return;
        }
        this.model = result.model;
        this.departmentInputs = result.departmentInputs;
        this.markDirty();
    }

    private currentDepartmentInput(
        company: DingTalkAllowlistModel["companies"][0],
        corpId: string,
    ): string {
        return (
            this.departmentInputs[corpId] ??
            renderDingTalkDepartmentInput(company.deptIds.map(String))
        );
    }

    private setDepartmentInput(corpId: string, value: string): void {
        if (!this.canManage) {
            return;
        }
        this.departmentInputs = {
            ...this.departmentInputs,
            [corpId]: value,
        };
        this.markDirty();
    }

    private applyDepartmentInput(corpId: string, departmentInput: string): void {
        if (!this.canManage) {
            return;
        }
        const company = this.model.companies.find((candidate) => candidate.corpId === corpId);
        if (!company || company.allowAll) {
            return;
        }
        const result = addDingTalkDepartments(
            this.model,
            { ...this.departmentInputs, [corpId]: departmentInput },
            corpId,
        );
        if (result.error) {
            showMessage({
                level: MessageLevel.error,
                message: result.invalidDepartmentId
                    ? msg(
                          str`${result.invalidDepartmentId} is not a valid DingTalk department ID.`,
                          {
                              id: "sources.oauth.dingtalk-allowlist.validation.department-id-invalid",
                          },
                      )
                    : result.error,
            });
            return;
        }
        this.model = result.model;
        this.departmentInputs = result.departmentInputs;
        this.markDirty();
    }

    private async discoverCompany(): Promise<void> {
        if (!this.source?.slug || !this.canManage) {
            return;
        }
        const sourceSlug = this.source.slug;
        // Open the popup synchronously within the user gesture; strict popup
        // blockers reject window.open calls made after an await.
        const popup = window.open(
            "about:blank",
            "authentik-dingtalk-discovery",
            "popup,width=640,height=760",
        );
        if (!popup) {
            showMessage({
                level: MessageLevel.error,
                message: msg(
                    "The DingTalk discovery popup was blocked. Allow popups for this site and try again.",
                    {
                        id: "sources.oauth.dingtalk-allowlist.discovery.popup-blocked",
                    },
                ),
            });
            return;
        }
        this.discoveryPopup = popup;
        try {
            const start = await this.sourcesApi.sourcesOauthDingtalkAllowlistDiscoverStartCreate({
                sourceSlug,
            });
            if (this.source?.slug !== sourceSlug) {
                this.finishDiscovery();
                return;
            }
            const discoveryUrl = validatedDingTalkDiscoveryUrl(start.url);
            if (!discoveryUrl) {
                throw new Error(
                    msg("DingTalk discovery failed.", {
                        id: "sources.oauth.dingtalk-allowlist.discovery.failed",
                    }),
                );
            }
            popup.location.assign(discoveryUrl);
        } catch (error) {
            popup.close();
            this.finishDiscovery();
            throw error;
        }
        // Keep the discovery action pending until the popup posts a result or the admin
        // closes it manually; polling `closed` clears the lingering popup reference and
        // stops the button from reporting success while discovery is still open.
        await new Promise<void>((resolve) => {
            this.discoveryDone = resolve;
            this.discoveryPoll = setInterval(() => {
                if (!this.discoveryPopup || this.discoveryPopup.closed) {
                    this.finishDiscovery();
                }
            }, DINGTALK_DISCOVERY_POLL_INTERVAL);
        });
    }

    // Loading departments only feeds the selection dialog; it never changes the
    // configured allowlist. Selection state stays entirely with the admin's input.
    private async loadDepartments(corpId: string): Promise<DingTalkDepartment[] | null> {
        if (!this.source?.slug) {
            return null;
        }
        const sourceSlug = this.source.slug;
        try {
            const response = await this.sourcesApi.sourcesOauthDingtalkAllowlistDepartmentsCreate({
                sourceSlug,
                dingTalkAllowlistDepartmentsRequestRequest: { corpId },
            });
            if (this.source?.slug !== sourceSlug) {
                return null;
            }
            const departments = normalizeDingTalkDepartments(response.departments);
            this.lastDepartmentFetch = {
                label: this.lastDepartmentFetch.label,
                state: "good",
                goodLabel: msg("Loaded", {
                    id: "sources.oauth.dingtalk-allowlist.status.good.loaded",
                }),
                detail: msg(str`${departments.length} departments loaded for ${corpId}`, {
                    id: "sources.oauth.dingtalk-allowlist.departments.loaded",
                }),
            };
            return departments;
        } catch (error) {
            if (this.source?.slug !== sourceSlug) {
                return null;
            }
            const detail = await this.apiErrorMessage(error);
            this.lastDepartmentFetch = dingtalkDepartmentFetchFailureStatus(
                this.lastDepartmentFetch,
                detail,
            );
            showMessage({
                level: MessageLevel.error,
                message: detail,
            });
            return null;
        }
    }

    // Fetches the directory fresh on every open (departments change server-side),
    // then hands selection editing to the modal picker. The allowlist model is only
    // touched when the admin applies the selection.
    private async pickDepartments(corpId: string): Promise<void> {
        if (!this.canManage) {
            return;
        }
        const company = this.model.companies.find((candidate) => candidate.corpId === corpId);
        if (!company || company.allowAll) {
            return;
        }
        const departments = await this.loadDepartments(corpId);
        if (!departments) {
            return;
        }
        const picker = new DingTalkDepartmentPickerModal();
        picker.headline = msg(
            str`Allowed departments for ${company.label || company.corpId} (${company.corpId})`,
            {
                id: "sources.oauth.dingtalk-allowlist.picker.headline",
            },
        );
        picker.departments = departments;
        picker.value = this.currentDepartmentInput(company, corpId);
        picker.onApply = (value) => this.applyDepartmentInput(corpId, value);
        await renderDialog(picker);
    }

    private async saveAndApply(): Promise<void> {
        const sourceSlug = this.source?.slug;
        if (!sourceSlug || !this.canManage) {
            return;
        }
        const departmentInputResult = applyDingTalkDepartmentInputs(
            this.model,
            this.departmentInputs,
        );
        if (departmentInputResult.error) {
            showMessage({
                level: MessageLevel.error,
                message: departmentInputResult.invalidDepartmentId
                    ? msg(
                          str`${departmentInputResult.invalidDepartmentId} is not a valid DingTalk department ID.`,
                          {
                              id: "sources.oauth.dingtalk-allowlist.validation.department-id-invalid",
                          },
                      )
                    : departmentInputResult.error,
            });
            return;
        }
        this.model = departmentInputResult.model;
        this.departmentInputs = departmentInputResult.departmentInputs;

        const result = await saveDingTalkAllowlistConfiguration({
            model: departmentInputResult.model,
            sourceSlug,
            expectedRevision: this.status?.revision ?? null,
            applyConfiguration: (model, expectedRevision) =>
                this.allowlistApi.sourcesOauthDingtalkAllowlistApplyCreate(sourceSlug, {
                    config: model,
                    expectedRevision,
                }),
            applyStatus: (status) => {
                if (this.source?.slug !== sourceSlug) return;
                this.applyBackendStatus(status);
                this.dirty = false;
            },
            onValidatedModel: (model) => {
                if (this.source?.slug === sourceSlug) this.model = model;
            },
        });

        if (!result || this.source?.slug !== sourceSlug) {
            return;
        }

        showMessage({
            level: MessageLevel.success,
            message: msg("DingTalk allowlist saved and applied.", {
                id: "sources.oauth.dingtalk-allowlist.save.success",
            }),
        });
    }

    private async removeManagedConfiguration(): Promise<void> {
        const sourceSlug = this.source?.slug;
        if (!sourceSlug || !this.canManage || !this.status?.managedPolicy._exists) {
            return;
        }
        const status = await this.allowlistApi.sourcesOauthDingtalkAllowlistRemoveCreate(
            sourceSlug,
            {
                expectedRevision: this.status.revision,
            },
        );
        this.applyBackendStatus(status);
        this.model = { companies: [] };
        this.departmentInputs = {};
        this.expressionValid = undefined;
        this.dirty = false;
    }

    private async confirmRemoveManagedConfiguration(event: Event): Promise<void> {
        if (!this.canManage) {
            return;
        }
        await confirmDingTalkDestructiveAction(
            {
                headline: msg("Remove DingTalk allowlist", {
                    id: "sources.oauth.dingtalk-allowlist.remove.header",
                }),
                body: html`<p>
                    ${msg(
                        "This deletes the managed allowlist policy and all of its flow bindings. All DingTalk logins will then be denied until a new allowlist is saved and applied.",
                        {
                            id: "sources.oauth.dingtalk-allowlist.remove.body",
                        },
                    )}
                </p>`,
                action: msg("Remove allowlist", {
                    id: "sources.oauth.dingtalk-allowlist.remove.action",
                }),
                successMessage: msg("DingTalk allowlist policy and bindings removed.", {
                    id: "sources.oauth.dingtalk-allowlist.remove.success",
                }),
                errorMessage: msg("Failed to remove the DingTalk allowlist policy.", {
                    id: "sources.oauth.dingtalk-allowlist.remove.error",
                }),
                onConfirm: () => this.removeManagedConfiguration(),
            },
            event.currentTarget instanceof HTMLElement ? event.currentTarget : undefined,
        );
    }

    private errorMessage(error: unknown): string {
        return this.localizeDingTalkDepartmentError(extractDingTalkAllowlistErrorCode(error));
    }

    private async apiErrorMessage(error: unknown): Promise<string> {
        const parsedError = await parseAPIResponseError(error);
        return this.localizeDingTalkDepartmentError(
            extractDingTalkAllowlistErrorCode(parsedError) ??
                extractDingTalkAllowlistErrorCode(error) ??
                extractDingTalkAllowlistErrorCode(pluckErrorDetail(parsedError)),
        );
    }

    private localizeDingTalkDepartmentError(code: DingTalkAllowlistErrorCode | null): string {
        switch (code) {
            case "department_access_denied":
                return msg(
                    "DingTalk departments can only be loaded for a company authorized by this DingTalk application. Edit the company label manually, or bind/authorize this company in the DingTalk developer console before loading departments.",
                    {
                        id: "sources.oauth.dingtalk-allowlist.departments.unauthorized-corp",
                    },
                );
            case "department_dependency_unavailable":
                return msg("Could not fetch DingTalk departments. Try again later.", {
                    id: "sources.oauth.dingtalk-allowlist.departments.load-failed",
                });
            case "department_response_invalid":
                return msg("DingTalk returned an invalid department response. Try again later.", {
                    id: "sources.oauth.dingtalk-allowlist.departments.response-invalid",
                });
            default:
                return msg("Could not fetch DingTalk departments. Try again later.", {
                    id: "sources.oauth.dingtalk-allowlist.departments.load-failed",
                });
        }
    }

    private localizeDingTalkDiscoveryError(code: DingTalkAllowlistErrorCode | null): string {
        switch (code) {
            case "state_invalid":
                return msg("DingTalk discovery expired or could not be verified. Try again.", {
                    id: "sources.oauth.dingtalk-allowlist.discovery.state-invalid",
                });
            case "state_expired":
                return msg("DingTalk discovery expired. Start discovery again.", {
                    id: "sources.oauth.dingtalk-allowlist.discovery.state-expired",
                });
            case "state_replayed":
                return msg(
                    "This DingTalk discovery result was already used. Start discovery again.",
                    {
                        id: "sources.oauth.dingtalk-allowlist.discovery.state-replayed",
                    },
                );
            case "state_source_mismatch":
                return msg(
                    "This DingTalk discovery result belongs to a different source. Start discovery again.",
                    {
                        id: "sources.oauth.dingtalk-allowlist.discovery.state-source-mismatch",
                    },
                );
            case "authorization_code_missing":
                return msg("DingTalk discovery did not return an authorization code. Try again.", {
                    id: "sources.oauth.dingtalk-allowlist.discovery.missing-code",
                });
            case "provider_unavailable":
                return msg("DingTalk discovery failed. Try again later.", {
                    id: "sources.oauth.dingtalk-allowlist.discovery.load-failed",
                });
            case "provider_response_invalid":
                return msg("DingTalk returned an invalid discovery response. Try again.", {
                    id: "sources.oauth.dingtalk-allowlist.discovery.response-invalid",
                });
            default:
                return msg("DingTalk discovery failed.", {
                    id: "sources.oauth.dingtalk-allowlist.discovery.failed",
                });
        }
    }

    private statusItems(): StatusItem[] {
        const stale = this.partialFailures.length > 0;
        const staleDetail = stale
            ? msg("Some DingTalk status checks failed. See the details below.", {
                  id: "sources.oauth.dingtalk-allowlist.status.refresh-failed",
              })
            : undefined;
        const staleState = (state: StatusItem["state"]): StatusItem["state"] =>
            stale && state === "good" ? "unknown" : state;
        const managedPolicy = this.status?.managedPolicy;
        const bindingStateForTarget = (target?: string | null): StatusItem["state"] =>
            target &&
            this.status?.policyBindings.some(
                (binding) => binding._exists && binding.enabled && binding.target === target,
            )
                ? "good"
                : "danger";

        return [
            {
                label: msg("Source enabled", {
                    id: "sources.oauth.dingtalk-allowlist.status.source-enabled",
                }),
                state: this.source?.enabled ? "good" : "danger",
                goodLabel: msg("Enabled", {
                    id: "sources.oauth.dingtalk-allowlist.status.good.enabled",
                }),
            },
            {
                label: msg("Managed policy exists", {
                    id: "sources.oauth.dingtalk-allowlist.status.policy-exists",
                }),
                state: staleState(managedPolicy?._exists ? "good" : "danger"),
                goodLabel: msg("Present", {
                    id: "sources.oauth.dingtalk-allowlist.status.good.present",
                }),
                detail: stale
                    ? staleDetail
                    : managedPolicy?._exists && managedPolicy.pk
                      ? html`<button
                            class="pf-c-button pf-m-link pf-m-inline"
                            type="button"
                            ${modalInvoker(ExpressionPolicyForm, { instancePk: managedPolicy.pk })}
                        >
                            ${managedPolicy.name}
                        </button>`
                      : undefined,
            },
            {
                label: msg("Expression validates", {
                    id: "sources.oauth.dingtalk-allowlist.status.expression-validates",
                }),
                state:
                    stale && this.expressionValid === true
                        ? "unknown"
                        : this.expressionValid === true
                          ? "good"
                          : this.expressionValid === false
                            ? "danger"
                            : "unknown",
                goodLabel: msg("Valid", {
                    id: "sources.oauth.dingtalk-allowlist.status.good.valid",
                }),
            },
            {
                label: msg("Authentication flow binding", {
                    id: "sources.oauth.dingtalk-allowlist.status.auth-binding",
                }),
                state: staleState(bindingStateForTarget(this.source?.authenticationFlow)),
                goodLabel: msg("Bound", {
                    id: "sources.oauth.dingtalk-allowlist.status.good.bound",
                }),
                detail: stale ? staleDetail : undefined,
            },
            {
                label: msg("Enrollment flow binding", {
                    id: "sources.oauth.dingtalk-allowlist.status.enrollment-binding",
                }),
                state: staleState(bindingStateForTarget(this.source?.enrollmentFlow)),
                goodLabel: msg("Bound", {
                    id: "sources.oauth.dingtalk-allowlist.status.good.bound",
                }),
                detail: stale ? staleDetail : undefined,
            },
            this.sourceLinkGuard,
            this.lastDepartmentFetch,
        ];
    }

    private renderStatus(item: StatusItem): TemplateResult {
        const { good, type } = dingtalkStatusLabelProperties(item.state);
        return html`<li>
            <ak-status-label
                type=${type}
                ?good=${good}
                good-label=${ifDefined(item.goodLabel)}
                bad-label=${ifDefined(
                    item.state === "unknown"
                        ? msg("Unknown", {
                              id: "sources.oauth.dingtalk-allowlist.status.unknown",
                          })
                        : undefined,
                )}
            ></ak-status-label>
            <span>${item.label}</span>
            ${item.detail ? html`<span class="ak-dingtalk-muted">${item.detail}</span>` : nothing}
        </li>`;
    }

    private renderManualAdd(): TemplateResult {
        const disabled = !this.canManage;
        return html`<div class="pf-c-form ak-dingtalk-section">
            <div class="pf-l-flex ak-dingtalk-inline-form">
                <div class="pf-c-form__group">
                    <label class="pf-c-form__label" for="dingtalk-manual-corp-id">
                        <span class="pf-c-form__label-text"
                            >${msg("Company corpId", {
                                id: "sources.oauth.dingtalk-allowlist.company.corp-id",
                            })}</span
                        >
                    </label>
                    <input
                        id="dingtalk-manual-corp-id"
                        class="pf-c-form-control"
                        ?disabled=${disabled}
                        .value=${this.manualCorpId}
                        @compositionstart=${this.startComposition}
                        @compositionend=${(event: CompositionEvent) =>
                            this.handleCompositionEnd(event, (value) => {
                                this.manualCorpId = value;
                            })}
                        @input=${(event: InputEvent) =>
                            this.handleComposedInput(event, (value) => {
                                this.manualCorpId = value;
                            })}
                        @keydown=${(event: KeyboardEvent) =>
                            this.handleSubmitKey(event, () => this.addManualCompany())}
                    />
                </div>
                <div class="pf-c-form__group">
                    <label class="pf-c-form__label" for="dingtalk-manual-label">
                        <span class="pf-c-form__label-text"
                            >${msg("Label", {
                                id: "sources.oauth.dingtalk-allowlist.company.label",
                            })}</span
                        >
                    </label>
                    <input
                        id="dingtalk-manual-label"
                        class="pf-c-form-control"
                        ?disabled=${disabled}
                        .value=${this.manualLabel}
                        @compositionstart=${this.startComposition}
                        @compositionend=${(event: CompositionEvent) =>
                            this.handleCompositionEnd(event, (value) => {
                                this.manualLabel = value;
                            })}
                        @input=${(event: InputEvent) =>
                            this.handleComposedInput(event, (value) => {
                                this.manualLabel = value;
                            })}
                        @keydown=${(event: KeyboardEvent) =>
                            this.handleSubmitKey(event, () => this.addManualCompany())}
                    />
                </div>
                <button
                    type="button"
                    class="pf-c-button pf-m-secondary"
                    ?disabled=${disabled}
                    @click=${() => this.addManualCompany()}
                >
                    ${msg("Add company", {
                        id: "sources.oauth.dingtalk-allowlist.company.add",
                    })}
                </button>
            </div>
        </div>`;
    }

    private renderCompanies(): TemplateResult {
        if (this.model.companies.length < 1) {
            return html`<ak-empty-state icon="pf-icon-enterprise">
                <span
                    >${msg("No DingTalk companies configured", {
                        id: "sources.oauth.dingtalk-allowlist.empty.title",
                    })}</span
                >
                <div slot="body">
                    ${msg("Discover a company or add one manually to build the allowlist.", {
                        id: "sources.oauth.dingtalk-allowlist.empty.body",
                    })}
                </div>
            </ak-empty-state>`;
        }

        return html`<table class="pf-c-table pf-m-compact pf-m-grid-md">
            <caption>
                ${msg("DingTalk allowlist companies", {
                    id: "sources.oauth.dingtalk-allowlist.table.caption",
                })}
            </caption>
            <thead>
                <tr>
                    <th scope="col">
                        ${msg("Company", { id: "sources.oauth.dingtalk-allowlist.table.company" })}
                    </th>
                    <th scope="col">
                        ${msg("Mode", { id: "sources.oauth.dingtalk-allowlist.table.mode" })}
                    </th>
                    <th scope="col">
                        ${msg("Department IDs", {
                            id: "sources.oauth.dingtalk-allowlist.table.departments",
                        })}
                    </th>
                    <th scope="col">
                        ${msg("Actions", { id: "sources.oauth.dingtalk-allowlist.table.actions" })}
                    </th>
                </tr>
            </thead>
            <tbody>
                ${this.model.companies.map((company) => this.renderCompanyRow(company))}
            </tbody>
        </table>`;
    }

    private renderCompanyRow(company: DingTalkAllowlistModel["companies"][0]): TemplateResult {
        const disabled = !this.canManage;
        const companyName = company.label || company.corpId;
        const missingDepartments = isDingTalkCompanyMissingDepartments(
            company,
            this.departmentInputs[company.corpId],
        );
        return html`<tr>
            <th
                scope="row"
                data-label=${msg("Company", {
                    id: "sources.oauth.dingtalk-allowlist.table.company",
                })}
            >
                <input
                    class="pf-c-form-control"
                    ?disabled=${disabled}
                    .value=${company.label}
                    aria-label=${msg(str`Company label for ${companyName} (${company.corpId})`, {
                        id: "sources.oauth.dingtalk-allowlist.company.label.aria-label",
                    })}
                    @compositionstart=${this.startComposition}
                    @compositionend=${(event: CompositionEvent) =>
                        this.handleCompositionEnd(event, (value) => {
                            this.updateCompany(company.corpId, { label: value });
                        })}
                    @input=${(event: InputEvent) =>
                        this.handleComposedInput(event, (value) => {
                            this.updateCompany(company.corpId, { label: value });
                        })}
                />
                <div class="ak-dingtalk-muted">${company.corpId}</div>
            </th>
            <td data-label=${msg("Mode", { id: "sources.oauth.dingtalk-allowlist.table.mode" })}>
                <label class="pf-c-switch">
                    <input
                        class="pf-c-switch__input"
                        type="checkbox"
                        ?disabled=${disabled}
                        .checked=${company.allowAll}
                        aria-label=${msg(
                            str`Allow full company access for ${companyName} (${company.corpId})`,
                            {
                                id: "sources.oauth.dingtalk-allowlist.company.allow-all.aria-label",
                            },
                        )}
                        @change=${(event: InputEvent) => {
                            this.updateCompany(company.corpId, {
                                allowAll: (event.target as HTMLInputElement).checked,
                            });
                        }}
                    />
                    <span class="pf-c-switch__toggle">
                        <span class="pf-c-switch__toggle-icon">
                            <i class="fas fa-check" aria-hidden="true"></i>
                        </span>
                    </span>
                    <span class="pf-c-switch__label">
                        ${msg("Allow full company", {
                            id: "sources.oauth.dingtalk-allowlist.company.allow-all",
                        })}
                    </span>
                </label>
                ${company.allowAll
                    ? html`<ak-alert inline plain level="warning" icon="fa-exclamation-triangle">
                          ${msg(
                              "Grants sign-in access to everyone in this company. Restrict to departments unless org-wide access is intended.",
                              {
                                  id: "sources.oauth.dingtalk-allowlist.company.allow-all.warning",
                              },
                          )}
                      </ak-alert>`
                    : nothing}
            </td>
            <td
                data-label=${msg("Department IDs", {
                    id: "sources.oauth.dingtalk-allowlist.table.departments",
                })}
            >
                ${company.allowAll
                    ? html`<span class="ak-dingtalk-muted"
                          >${msg("Ignored while full company is allowed", {
                              id: "sources.oauth.dingtalk-allowlist.company.departments-ignored",
                          })}</span
                      >`
                    : nothing}
                ${missingDepartments
                    ? html`<ak-alert inline plain level="warning" icon="fa-exclamation-triangle">
                          ${msg(
                              "Restricted mode needs at least one department. Add a department or enable full company access before saving.",
                              {
                                  id: "sources.oauth.dingtalk-allowlist.company.departments-required.warning",
                              },
                          )}
                      </ak-alert>`
                    : nothing}
                <div class="ak-dingtalk-input-row">
                    <input
                        class="pf-c-form-control ak-dingtalk-department-input"
                        ?disabled=${disabled || company.allowAll}
                        aria-label=${msg(
                            str`Allowed department IDs for ${companyName} (${company.corpId})`,
                            {
                                id: "sources.oauth.dingtalk-allowlist.departments.input.aria-label",
                            },
                        )}
                        .value=${this.departmentInputs[company.corpId] ??
                        renderDingTalkDepartmentInput(company.deptIds.map(String))}
                        placeholder=${msg("IDs separated by commas or spaces", {
                            id: "sources.oauth.dingtalk-allowlist.departments.placeholder",
                        })}
                        @compositionstart=${this.startComposition}
                        @compositionend=${(event: CompositionEvent) =>
                            this.handleCompositionEnd(event, (value) =>
                                this.setDepartmentInput(company.corpId, value),
                            )}
                        @input=${(event: InputEvent) =>
                            this.handleComposedInput(event, (value) =>
                                this.setDepartmentInput(company.corpId, value),
                            )}
                        @keydown=${(event: KeyboardEvent) =>
                            this.handleSubmitKey(event, () => this.addDepartments(company.corpId))}
                    />
                    <button
                        type="button"
                        class="pf-c-button pf-m-secondary"
                        ?disabled=${disabled || company.allowAll}
                        aria-label=${msg(
                            str`Add departments for ${companyName} (${company.corpId})`,
                            {
                                id: "sources.oauth.dingtalk-allowlist.departments.add.aria-label",
                            },
                        )}
                        @click=${() => this.addDepartments(company.corpId)}
                    >
                        ${msg("Add departments", {
                            id: "sources.oauth.dingtalk-allowlist.departments.add",
                        })}
                    </button>
                    <ak-spinner-button
                        class="pf-m-secondary"
                        ?disabled=${disabled || company.allowAll}
                        aria-label=${msg(
                            str`Select departments for ${companyName} (${company.corpId})`,
                            {
                                id: "sources.oauth.dingtalk-allowlist.departments.select.aria-label",
                            },
                        )}
                        .callAction=${() => this.pickDepartments(company.corpId)}
                    >
                        ${msg("Select departments…", {
                            id: "sources.oauth.dingtalk-allowlist.departments.select",
                        })}
                    </ak-spinner-button>
                </div>
            </td>
            <td
                data-label=${msg("Actions", {
                    id: "sources.oauth.dingtalk-allowlist.table.actions",
                })}
            >
                ${this.canManage
                    ? html`<div class="ak-dingtalk-table-actions">
                          <button
                              type="button"
                              class="pf-c-button pf-m-danger"
                              aria-label=${msg(str`Remove ${companyName} (${company.corpId})`, {
                                  id: "sources.oauth.dingtalk-allowlist.company.remove.aria-label",
                              })}
                              @click=${() => this.removeCompany(company.corpId)}
                          >
                              ${msg("Remove", { id: "common.actions.remove" })}
                          </button>
                      </div>`
                    : nothing}
            </td>
        </tr>`;
    }

    private renderDiscoveryDetails(): TemplateResult {
        if (!this.lastDiscovery) {
            return html``;
        }
        return html`<p class="ak-dingtalk-muted">
            ${msg(str`Last discovery: ${this.lastDiscovery.corpId}`, {
                id: "sources.oauth.dingtalk-allowlist.discovery.last",
            })}
            ${this.lastDiscovery.userId
                ? html`${msg(str`User ID ${this.lastDiscovery.userId}`, {
                      id: "sources.oauth.dingtalk-allowlist.discovery.user-id",
                  })}`
                : nothing}
        </p>`;
    }

    private renderUnsavedChanges(): TemplateResult {
        if (!this.dirty) {
            return html``;
        }
        return html`<ak-alert class="ak-dingtalk-section" level="warning" icon="fa-pencil">
            ${msg("You have unsaved allowlist changes. Use Save and apply to persist them.", {
                id: "sources.oauth.dingtalk-allowlist.unsaved.title",
            })}
        </ak-alert>`;
    }

    // DingTalk logins fail closed: without a saved allowlist the backend denies
    // every DingTalk sign-in, so surface that state prominently instead of letting
    // an empty panel read as "no restrictions".
    private renderFailClosedNotice(): TemplateResult {
        if (this.status?.managedPolicy._exists) {
            return html``;
        }
        return html`<ak-alert class="ak-dingtalk-section" level="warning" icon="fa-ban">
            ${msg(
                "No allowlist is configured. All DingTalk logins are denied until an allowlist is saved and applied.",
                {
                    id: "sources.oauth.dingtalk-allowlist.fail-closed.unconfigured",
                },
            )}
        </ak-alert>`;
    }

    private renderDetectedSharedConfig(): TemplateResult {
        const detected = this.detectedSharedConfig;
        if (!detected || detected.companies.length < 1) {
            return html``;
        }
        return html`<ak-alert
            class="ak-dingtalk-section"
            level="warning"
            icon="fa-exclamation-triangle"
        >
            <strong
                >${msg("Companies detected on a shared flow", {
                    id: "sources.oauth.dingtalk-allowlist.detected.title",
                })}</strong
            >
            <p>
                ${msg(
                    "These companies come from another DingTalk source that shares this flow. They are not part of this source's allowlist and will not be saved unless you adopt them.",
                    {
                        id: "sources.oauth.dingtalk-allowlist.detected.body",
                    },
                )}
            </p>
            <ul>
                ${detected.companies.map(
                    (company) =>
                        html`<li>
                            ${company.label || company.corpId}
                            <span class="ak-dingtalk-muted">(${company.corpId})</span>
                        </li>`,
                )}
            </ul>
            <button
                type="button"
                class="pf-c-button pf-m-secondary pf-m-small"
                ?disabled=${!this.canManage}
                @click=${() => this.adoptDetectedSharedConfig()}
            >
                ${msg("Adopt detected companies", {
                    id: "sources.oauth.dingtalk-allowlist.detected.adopt",
                })}
            </button>
        </ak-alert>`;
    }

    private renderPartialFailures(): TemplateResult {
        if (this.partialFailures.length < 1) {
            return html``;
        }
        return html`<ak-alert
            class="ak-dingtalk-section"
            level="danger"
            icon="fa-exclamation-circle"
        >
            <strong
                >${msg("Some DingTalk status checks failed.", {
                    id: "sources.oauth.dingtalk-allowlist.partial-failures.title",
                })}</strong
            >
            <ul>
                ${this.partialFailures.map((failure) => html`<li>${failure}</li>`)}
            </ul>
        </ak-alert>`;
    }

    private renderRemoveConfiguration(): TemplateResult {
        if (!this.status?.managedPolicy._exists || !this.canManage) {
            return html``;
        }
        return html`<button
            class="pf-c-button pf-m-danger pf-m-secondary"
            type="button"
            @click=${(event: Event) => this.confirmRemoveManagedConfiguration(event)}
        >
            ${msg("Remove allowlist", {
                id: "sources.oauth.dingtalk-allowlist.remove.action",
            })}
        </button>`;
    }

    render(): SlottedTemplateResult {
        if (!this.source) {
            return nothing;
        }
        return html`<div class="pf-l-grid pf-m-gutter">
            <div class="pf-c-card pf-l-grid__item pf-m-12-col">
                <div class="pf-c-card__title">
                    ${msg("DingTalk Allowlist", {
                        id: "sources.oauth.dingtalk-allowlist.title",
                    })}
                </div>
                <div class="pf-c-card__body">
                    <div class="pf-l-flex ak-dingtalk-actions ak-dingtalk-section">
                        <ak-spinner-button
                            class="pf-m-secondary"
                            ?disabled=${!this.loaded || !this.canManage}
                            .callAction=${() => this.discoverCompany()}
                        >
                            ${msg("Discover company", {
                                id: "sources.oauth.dingtalk-allowlist.discovery.start",
                            })}
                        </ak-spinner-button>
                        <ak-spinner-button
                            class="pf-m-primary"
                            ?disabled=${!this.loaded || !this.canManage}
                            .callAction=${() => this.saveAndApply()}
                        >
                            ${msg("Save and apply", {
                                id: "sources.oauth.dingtalk-allowlist.save.apply",
                            })}
                        </ak-spinner-button>
                        <ak-spinner-button
                            class="pf-m-secondary"
                            .callAction=${() => this.refreshStatusAction()}
                        >
                            ${msg("Refresh status", {
                                id: "sources.oauth.dingtalk-allowlist.status.refresh",
                            })}
                        </ak-spinner-button>
                        ${this.renderRemoveConfiguration()}
                    </div>
                    ${this.loaded
                        ? html`${this.canManage ? nothing : this.readOnlyNotice}
                              ${this.renderFailClosedNotice()} ${this.renderUnsavedChanges()}
                              ${this.renderDiscoveryDetails()}
                              <ul class="ak-dingtalk-status ak-dingtalk-section">
                                  ${this.statusItems().map((item) => this.renderStatus(item))}
                              </ul>
                              ${this.renderDetectedSharedConfig()} ${this.renderPartialFailures()}
                              ${this.renderManualAdd()} ${this.renderCompanies()}`
                        : html`<ak-empty-state loading
                              ><span
                                  >${msg("Loading DingTalk allowlist status…", {
                                      id: "sources.oauth.dingtalk-allowlist.status.loading",
                                  })}</span
                              ></ak-empty-state
                          >`}
                </div>
            </div>
        </div>`;
    }
}

declare global {
    interface HTMLElementTagNameMap {
        "ak-source-oauth-dingtalk-allowlist": DingTalkAllowlistPanel;
    }
}
