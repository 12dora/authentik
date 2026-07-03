import "#components/ak-status-label";
import "#elements/buttons/SpinnerButton/index";
import "#elements/forms/ConfirmationForm";
import "#elements/EmptyState";

import { DEFAULT_CONFIG } from "#common/api/config";
import { parseAPIResponseError, pluckErrorDetail } from "#common/errors/network";
import { MessageLevel } from "#common/messages";

import { AKElement } from "#elements/Base";
import { modalInvoker } from "#elements/dialogs";
import { showMessage } from "#elements/messages/MessageContainer";
import { SlottedTemplateResult } from "#elements/types";

import { ExpressionPolicyForm } from "#admin/policies/expression/ExpressionPolicyForm";
import type { StatusItem } from "#admin/sources/oauth/DingTalkAllowlistPanelState";
import {
    addDingTalkDepartments,
    applyDingTalkDepartmentInputs,
    buildDingTalkDepartmentTreeRows,
    dingtalkDepartmentFetchFailureStatus,
    dingtalkDepartmentInputsFromModel,
    dingtalkStatusLabelProperties,
    invertLoadedDingTalkDepartmentInput,
    removeDingTalkCompany,
    renderDingTalkDepartmentInput,
    saveDingTalkAllowlistConfiguration,
    selectLoadedDingTalkDepartmentInput,
    splitDingTalkDepartmentIds,
    toggleDingTalkDepartmentTreeInput,
    updateDingTalkCompany,
    upsertDingTalkCompany,
} from "#admin/sources/oauth/DingTalkAllowlistPanelState";
import {
    DingTalkAllowlistModel,
    dingTalkAllowlistModelFromStoredConfig,
    hasDingTalkAllowlistPolicyMarker,
    parseDingTalkAllowlistPolicy,
} from "#admin/sources/oauth/DingTalkAllowlistPolicy";

import {
    DingTalkAllowlistStatusResponse,
    ExpressionPolicy,
    Flow,
    FlowsApi,
    OAuthSource,
    PoliciesApi,
    PolicyBinding,
    SourcesApi,
} from "@goauthentik/api";

import { msg, str } from "@lit/localize";
import { css, CSSResult, html, nothing, PropertyValues, TemplateResult } from "lit";
import { customElement, property, state } from "lit/decorators.js";
import { ifDefined } from "lit/directives/if-defined.js";

import PFButton from "@patternfly/patternfly/components/Button/button.css";
import PFCard from "@patternfly/patternfly/components/Card/card.css";
import PFContent from "@patternfly/patternfly/components/Content/content.css";
import PFForm from "@patternfly/patternfly/components/Form/form.css";
import PFFormControl from "@patternfly/patternfly/components/FormControl/form-control.css";
import PFTable from "@patternfly/patternfly/components/Table/table.css";
import PFFlex from "@patternfly/patternfly/layouts/Flex/flex.css";

const DINGTALK_DISCOVERY_MESSAGE_SOURCE = "goauthentik.io";
const DINGTALK_DISCOVERY_MESSAGE_CONTEXT = "dingtalk-allowlist-discovery";

interface DingTalkDiscoveryResult {
    corpId: string;
    label?: string;
    userId?: string;
}

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

@customElement("ak-source-oauth-dingtalk-allowlist")
export class DingTalkAllowlistPanel extends AKElement {
    @property({ attribute: false })
    public source?: OAuthSource;

    @state()
    private model: DingTalkAllowlistModel = { companies: [] };

    @state()
    private policy?: ExpressionPolicy;

    @state()
    private authFlow?: Flow;

    @state()
    private enrollmentFlow?: Flow;

    @state()
    private authBinding?: PolicyBinding;

    @state()
    private enrollmentBinding?: PolicyBinding;

    @state()
    private manualCorpId = "";

    @state()
    private manualLabel = "";

    @state()
    private departmentInputs: Record<string, string> = {};

    @state()
    private fetchedDepartments: Record<string, DingTalkDepartment[]> = {};

    @state()
    private lastDiscovery?: DingTalkDiscoveryResult;

    @state()
    private lastDepartmentFetch: StatusItem = {
        label: msg("Last department fetch", {
            id: "sources.oauth.dingtalk-allowlist.status.departments.label",
        }),
        state: "unknown",
        detail: msg("Not run", { id: "sources.oauth.dingtalk-allowlist.status.not-run" }),
    };

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

    // Set once the admin edits the local allowlist; while set, status refreshes keep
    // the local model and inputs instead of overwriting them with server state.
    private dirty = false;

    // Incremented per refresh; stale refreshes must not overwrite newer state.
    private refreshGeneration = 0;

    private discoveryPopup: Window | null = null;

    private policiesApi = new PoliciesApi(DEFAULT_CONFIG);
    private flowsApi = new FlowsApi(DEFAULT_CONFIG);
    private sourcesApi = new SourcesApi(DEFAULT_CONFIG);

    static styles: CSSResult[] = [
        PFButton,
        PFCard,
        PFContent,
        PFForm,
        PFFormControl,
        PFTable,
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

            .ak-dingtalk-department-actions {
                display: flex;
                gap: var(--pf-global--spacer--sm);
                margin-block: var(--pf-global--spacer--sm);
            }

            .ak-dingtalk-department-tree-cell {
                display: flex;
                align-items: center;
                gap: var(--pf-global--spacer--sm);
            }
        `,
    ];

    connectedCallback(): void {
        super.connectedCallback();
        window.addEventListener("message", this.handleDiscoveryMessage);
    }

    disconnectedCallback(): void {
        window.removeEventListener("message", this.handleDiscoveryMessage);
        super.disconnectedCallback();
    }

    protected willUpdate(changedProperties: PropertyValues<this>): void {
        if (changedProperties.has("source") && this.source?.slug) {
            // Only a different source warrants an automatic refresh; the same source
            // object is re-assigned after saves and global refresh events, and
            // refreshing again would race the explicit refresh already in flight.
            const previous = changedProperties.get("source") as OAuthSource | undefined;
            if (previous?.slug !== this.source.slug) {
                this.refreshStatus().catch(console.error);
            }
        }
    }

    private get policyName(): string {
        return `dingtalk-allowlist-${this.source?.slug || ""}`;
    }

    private markDirty(): void {
        this.dirty = true;
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
        this.discoveryPopup = null;
        if (record.ok === false) {
            showMessage({
                level: MessageLevel.error,
                message:
                    normalizeOptionalString(record.error) ||
                    msg("DingTalk discovery failed.", {
                        id: "sources.oauth.dingtalk-allowlist.discovery.failed",
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
        this.upsertCompany(result.corpId, result.label || result.corpId, true, []);
        showMessage({
            level: MessageLevel.success,
            message: msg(str`Discovered DingTalk company ${result.corpId}`, {
                id: "sources.oauth.dingtalk-allowlist.discovery.success",
            }),
        });
    };

    private extractDiscoveryResult(
        record: Record<string, unknown>,
    ): DingTalkDiscoveryResult | null {
        const payload =
            record.profile && typeof record.profile === "object"
                ? (record.profile as Record<string, unknown>)
                : record;
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
        const generation = ++this.refreshGeneration;
        const failures: string[] = [];
        let modelFromPolicy = false;

        try {
            modelFromPolicy = await this.refreshManagedPolicy(generation);
        } catch (error) {
            failures.push(await this.apiErrorMessage(error));
        }

        try {
            await this.refreshFlowsAndBindings(generation);
        } catch (error) {
            failures.push(await this.apiErrorMessage(error));
        }

        try {
            const status = await this.sourcesApi.sourcesOauthDingtalkAllowlistStatusRetrieve({
                sourceSlug: this.source.slug,
            });
            if (generation === this.refreshGeneration) {
                this.applyBackendStatus(status, modelFromPolicy);
            }
        } catch (error) {
            if (generation === this.refreshGeneration) {
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

        if (generation === this.refreshGeneration) {
            this.partialFailures = failures;
        }
    }

    private async refreshManagedPolicy(generation: number): Promise<boolean> {
        const response = await this.policiesApi.policiesExpressionList({
            name: this.policyName,
            pageSize: 1,
        });
        if (generation !== this.refreshGeneration) {
            return false;
        }
        this.policy = response.results.find((policy) => policy.name === this.policyName);
        if (!this.policy) {
            this.expressionValid = undefined;
            return false;
        }
        const parsed = parseDingTalkAllowlistPolicy(this.policy.expression);
        if (!parsed) {
            this.expressionValid = false;
            return false;
        }
        this.expressionValid = true;
        if (!this.dirty) {
            this.model = parsed;
            this.departmentInputs = dingtalkDepartmentInputsFromModel(parsed);
        }
        return true;
    }

    private async refreshFlowsAndBindings(generation: number): Promise<void> {
        const authFlow = await this.resolveFlow(this.source?.authenticationFlow);
        const enrollmentFlow = await this.resolveFlow(this.source?.enrollmentFlow);

        const [authBinding, enrollmentBinding] = await Promise.all([
            this.findPolicyBinding(authFlow?.policybindingmodelPtrId, this.policy?.pk),
            this.findPolicyBinding(enrollmentFlow?.policybindingmodelPtrId, this.policy?.pk),
        ]);

        if (generation !== this.refreshGeneration) {
            return;
        }
        this.authFlow = authFlow;
        this.enrollmentFlow = enrollmentFlow;
        this.authBinding = authBinding;
        this.enrollmentBinding = enrollmentBinding;
    }

    private applyBackendStatus(
        status: DingTalkAllowlistStatusResponse,
        modelFromPolicy: boolean,
    ): void {
        // The status config is discovered by walking every binding on the shared
        // flows and can belong to another DingTalk source; it is only a fallback
        // when this source's own managed policy did not provide a model.
        const config = status.config as { companies?: unknown } | null;
        if (
            !modelFromPolicy &&
            !this.dirty &&
            config &&
            Array.isArray(config.companies) &&
            config.companies.length > 0
        ) {
            const model = dingTalkAllowlistModelFromStoredConfig(config);
            if (model) {
                this.model = model;
                this.departmentInputs = dingtalkDepartmentInputsFromModel(model);
            }
        }

        const enabled = status.sourceLinkGuard?.enabled ?? false;
        this.sourceLinkGuard = {
            label: this.sourceLinkGuard.label,
            state: enabled ? "good" : "danger",
            detail: enabled
                ? msg("Installed or disabled", {
                      id: "sources.oauth.dingtalk-allowlist.status.source-link.good",
                  })
                : msg("Needs review", {
                      id: "sources.oauth.dingtalk-allowlist.status.source-link.review",
                  }),
        };
    }

    private async resolveFlow(flowPk?: string | null): Promise<Flow | undefined> {
        if (!flowPk) {
            return undefined;
        }
        const response = await this.flowsApi.flowsInstancesList({ flowUuid: flowPk, pageSize: 1 });
        return response.results[0];
    }

    private async findPolicyBinding(
        target?: string,
        policyPk?: string,
    ): Promise<PolicyBinding | undefined> {
        if (!target || !policyPk) {
            return undefined;
        }
        const response = await this.policiesApi.policiesBindingsList({
            target,
            policy: policyPk,
            pageSize: 20,
        });
        return response.results.find((binding) => binding.policy === policyPk);
    }

    private upsertCompany(
        corpId: string,
        label: string,
        allowAll: boolean,
        deptIds: string[],
    ): void {
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
        this.upsertCompany(corpId, normalizeOptionalString(this.manualLabel) || corpId, true, []);
        this.manualCorpId = "";
        this.manualLabel = "";
    }

    private updateCompany(corpId: string, patch: Partial<DingTalkAllowlistModel["companies"][0]>) {
        this.model = updateDingTalkCompany(this.model, corpId, patch);
        this.markDirty();
    }

    private removeCompany(corpId: string): void {
        this.model = removeDingTalkCompany(this.model, corpId);
        this.markDirty();
    }

    private addDepartments(corpId: string): void {
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

    private applyDepartmentInput(corpId: string, departmentInput: string): void {
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
            return;
        }
        this.model = result.model;
        this.departmentInputs = result.departmentInputs;
        this.markDirty();
    }

    private toggleLoadedDepartment(corpId: string, deptId: string, selected: boolean): void {
        const company = this.model.companies.find((candidate) => candidate.corpId === corpId);
        if (!company || company.allowAll) {
            return;
        }
        this.applyDepartmentInput(
            corpId,
            toggleDingTalkDepartmentTreeInput(
                this.currentDepartmentInput(company, corpId),
                this.fetchedDepartments[corpId] || [],
                deptId,
                selected,
            ),
        );
    }

    private selectAllLoadedDepartments(corpId: string): void {
        const company = this.model.companies.find((candidate) => candidate.corpId === corpId);
        if (!company || company.allowAll) {
            return;
        }
        this.applyDepartmentInput(
            corpId,
            selectLoadedDingTalkDepartmentInput(
                this.currentDepartmentInput(company, corpId),
                this.fetchedDepartments[corpId] || [],
            ),
        );
    }

    private invertLoadedDepartments(corpId: string): void {
        const company = this.model.companies.find((candidate) => candidate.corpId === corpId);
        if (!company || company.allowAll) {
            return;
        }
        this.applyDepartmentInput(
            corpId,
            invertLoadedDingTalkDepartmentInput(
                this.currentDepartmentInput(company, corpId),
                this.fetchedDepartments[corpId] || [],
            ),
        );
    }

    private async discoverCompany(): Promise<void> {
        if (!this.source?.slug) {
            return;
        }
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
                sourceSlug: this.source.slug,
            });
            popup.location.assign(start.url);
        } catch (error) {
            popup.close();
            this.discoveryPopup = null;
            throw error;
        }
    }

    // Loading departments only populates the selection tree; it never changes the
    // configured allowlist. Selection state stays entirely with the admin's input.
    private async loadDepartments(corpId: string): Promise<void> {
        if (!this.source?.slug) {
            return;
        }
        try {
            const response = await this.sourcesApi.sourcesOauthDingtalkAllowlistDepartmentsCreate({
                sourceSlug: this.source.slug,
                dingTalkAllowlistDepartmentsRequestRequest: { corpId },
            });
            const departments = normalizeDingTalkDepartments(response.departments);
            this.fetchedDepartments = {
                ...this.fetchedDepartments,
                [corpId]: departments,
            };
            this.lastDepartmentFetch = {
                label: this.lastDepartmentFetch.label,
                state: "good",
                detail: msg(str`${departments.length} departments loaded for ${corpId}`, {
                    id: "sources.oauth.dingtalk-allowlist.departments.loaded",
                }),
            };
        } catch (error) {
            const detail = await this.apiErrorMessage(error);
            this.lastDepartmentFetch = dingtalkDepartmentFetchFailureStatus(
                this.lastDepartmentFetch,
                detail,
            );
            showMessage({
                level: MessageLevel.error,
                message: detail,
            });
        }
    }

    private async saveAndApply(): Promise<void> {
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
            sourceSlug: this.source?.slug,
            createOrUpdatePolicy: (expression) => this.createOrUpdatePolicy(expression),
            retrieveSource: (slug) =>
                this.sourcesApi.sourcesOauthRetrieve({
                    slug,
                }),
            getAuthenticationFlowPk: (source) => source.authenticationFlow,
            getEnrollmentFlowPk: (source) => source.enrollmentFlow,
            resolveFlow: (flowPk) => this.resolveFlow(flowPk),
            ensureBinding: (flow, policy) => this.ensureBinding(flow, policy),
            refreshStatus: () => this.refreshStatus(),
            bindingFailureLabel: (kind) =>
                kind === "authentication"
                    ? msg("Authentication flow binding", {
                          id: "sources.oauth.dingtalk-allowlist.binding.auth.label",
                      })
                    : msg("Enrollment flow binding", {
                          id: "sources.oauth.dingtalk-allowlist.binding.enrollment.label",
                      }),
            errorMessage: (error) => this.errorMessage(error),
            onValidatedModel: (model) => {
                this.model = model;
            },
            onPolicySaved: (policy) => {
                this.policy = policy;
                this.expressionValid = true;
                // The configured allowlist is persisted; refreshes may take over again.
                this.dirty = false;
            },
            onSourceRefreshed: (source) => {
                this.source = source;
            },
            onFlowsResolved: (authFlow, enrollmentFlow) => {
                this.authFlow = authFlow;
                this.enrollmentFlow = enrollmentFlow;
            },
        });

        if (!result) {
            return;
        }

        this.partialFailures = [...result.failures, ...this.partialFailures];

        showMessage({
            level: result.failures.length > 0 ? MessageLevel.warning : MessageLevel.success,
            message:
                result.failures.length > 0
                    ? msg("DingTalk allowlist saved with binding warnings.", {
                          id: "sources.oauth.dingtalk-allowlist.save.partial",
                      })
                    : msg("DingTalk allowlist saved and applied.", {
                          id: "sources.oauth.dingtalk-allowlist.save.success",
                      }),
        });
    }

    private async createOrUpdatePolicy(expression: string): Promise<ExpressionPolicy> {
        const existing = await this.policiesApi.policiesExpressionList({
            name: this.policyName,
            pageSize: 1,
        });
        const policy = existing.results.find((candidate) => candidate.name === this.policyName);
        if (!policy) {
            return this.policiesApi.policiesExpressionCreate({
                expressionPolicyRequest: {
                    name: this.policyName,
                    expression,
                    executionLogging: false,
                },
            });
        }
        if (!hasDingTalkAllowlistPolicyMarker(policy.expression)) {
            throw new Error(
                msg(
                    "A policy with the managed DingTalk allowlist name exists but does not contain the managed marker.",
                    {
                        id: "sources.oauth.dingtalk-allowlist.policy.unmanaged-existing-policy",
                    },
                ),
            );
        }
        return this.policiesApi.policiesExpressionPartialUpdate({
            policyUuid: policy.pk,
            patchedExpressionPolicyRequest: {
                expression,
                executionLogging: policy.executionLogging ?? false,
            },
        });
    }

    private async ensureBinding(flow: Flow | undefined, policy: ExpressionPolicy): Promise<void> {
        if (!flow?.policybindingmodelPtrId) {
            throw new Error(
                msg("Flow is not configured on this source.", {
                    id: "sources.oauth.dingtalk-allowlist.binding.flow-not-configured",
                }),
            );
        }

        try {
            // List every binding on the flow (not just this policy's) so a newly
            // created binding gets an order after the existing ones.
            const existing = await this.policiesApi.policiesBindingsList({
                target: flow.policybindingmodelPtrId,
                pageSize: 100,
            });
            const current = existing.results.find((binding) => binding.policy === policy.pk);
            if (current) {
                // Only re-enable a disabled binding; timeout, order, and failure
                // handling stay whatever the admin configured.
                if (!current.enabled) {
                    await this.policiesApi.policiesBindingsPartialUpdate({
                        policyBindingUuid: current.pk,
                        patchedPolicyBindingRequest: {
                            enabled: true,
                        },
                    });
                }
                return;
            }

            const nextOrder =
                existing.results.reduce((order, binding) => Math.max(order, binding.order), 0) + 10;
            await this.policiesApi.policiesBindingsCreate({
                policyBindingRequest: {
                    target: flow.policybindingmodelPtrId,
                    policy: policy.pk,
                    enabled: true,
                    order: nextOrder,
                    timeout: 30,
                    failureResult: false,
                },
            });
        } catch (error) {
            throw new Error(await this.apiErrorMessage(error));
        }
    }

    private async removeManagedConfiguration(): Promise<void> {
        const policy = this.policy;
        if (!policy) {
            return;
        }
        const bindings = await this.policiesApi.policiesBindingsList({
            policy: policy.pk,
            pageSize: 100,
        });
        for (const binding of bindings.results) {
            await this.policiesApi.policiesBindingsDestroy({
                policyBindingUuid: binding.pk,
            });
        }
        await this.policiesApi.policiesExpressionDestroy({
            policyUuid: policy.pk,
        });
        this.policy = undefined;
        this.authBinding = undefined;
        this.enrollmentBinding = undefined;
        this.model = { companies: [] };
        this.departmentInputs = {};
        this.expressionValid = undefined;
        this.dirty = false;
        await this.refreshStatus();
    }

    private errorMessage(error: unknown): string {
        if (error instanceof Error) {
            return this.localizeDingTalkDepartmentError(error.message);
        }
        return this.localizeDingTalkDepartmentError(String(error));
    }

    private async apiErrorMessage(error: unknown): Promise<string> {
        const parsedError = await parseAPIResponseError(error);
        return this.localizeDingTalkDepartmentError(pluckErrorDetail(parsedError));
    }

    private localizeDingTalkDepartmentError(detail: string): string {
        if (
            detail.includes(
                "DingTalk departments can only be loaded for a company authorized by this DingTalk application",
            )
        ) {
            return msg(
                "DingTalk departments can only be loaded for a company authorized by this DingTalk application. Edit the company label manually, or bind/authorize this company in the DingTalk developer console before loading departments.",
                {
                    id: "sources.oauth.dingtalk-allowlist.departments.unauthorized-corp",
                },
            );
        }
        if (detail) {
            return detail;
        }
        return msg("An unknown error occurred.", {
            id: "sources.oauth.dingtalk-allowlist.departments.unknown-error",
        });
    }

    private statusItems(): StatusItem[] {
        return [
            {
                label: msg("Source enabled", {
                    id: "sources.oauth.dingtalk-allowlist.status.source-enabled",
                }),
                state: this.source?.enabled ? "good" : "danger",
            },
            {
                label: msg("Managed policy exists", {
                    id: "sources.oauth.dingtalk-allowlist.status.policy-exists",
                }),
                state: this.policy ? "good" : "danger",
                detail: this.policy
                    ? html`<button
                          class="pf-c-button pf-m-link pf-m-inline"
                          type="button"
                          ${modalInvoker(ExpressionPolicyForm, { instancePk: this.policy.pk })}
                      >
                          ${this.policy.name}
                      </button>`
                    : undefined,
            },
            {
                label: msg("Expression validates", {
                    id: "sources.oauth.dingtalk-allowlist.status.expression-validates",
                }),
                state:
                    this.expressionValid === true
                        ? "good"
                        : this.expressionValid === false
                          ? "danger"
                          : "unknown",
            },
            {
                label: msg("Authentication flow binding", {
                    id: "sources.oauth.dingtalk-allowlist.status.auth-binding",
                }),
                state: this.authBinding?.enabled ? "good" : "danger",
                detail: this.authFlow
                    ? html`<a href=${`#/flow/flows/${this.authFlow.slug}`}
                          >${this.authFlow.name}</a
                      >`
                    : msg("No flow configured", {
                          id: "sources.oauth.dingtalk-allowlist.status.no-flow",
                      }),
            },
            {
                label: msg("Enrollment flow binding", {
                    id: "sources.oauth.dingtalk-allowlist.status.enrollment-binding",
                }),
                state: this.enrollmentBinding?.enabled ? "good" : "danger",
                detail: this.enrollmentFlow
                    ? html`<a href=${`#/flow/flows/${this.enrollmentFlow.slug}`}
                          >${this.enrollmentFlow.name}</a
                      >`
                    : msg("No flow configured", {
                          id: "sources.oauth.dingtalk-allowlist.status.no-enrollment-flow",
                      }),
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
        return html`<div class="pf-c-form ak-dingtalk-section">
            <div class="pf-l-flex ak-dingtalk-inline-form">
                <div class="pf-c-form__group">
                    <label class="pf-c-form__label">
                        <span class="pf-c-form__label-text"
                            >${msg("Company corpId", {
                                id: "sources.oauth.dingtalk-allowlist.company.corp-id",
                            })}</span
                        >
                    </label>
                    <input
                        class="pf-c-form-control"
                        .value=${this.manualCorpId}
                        @input=${(event: InputEvent) => {
                            this.manualCorpId = (event.target as HTMLInputElement).value;
                        }}
                    />
                </div>
                <div class="pf-c-form__group">
                    <label class="pf-c-form__label">
                        <span class="pf-c-form__label-text"
                            >${msg("Label", {
                                id: "sources.oauth.dingtalk-allowlist.company.label",
                            })}</span
                        >
                    </label>
                    <input
                        class="pf-c-form-control"
                        .value=${this.manualLabel}
                        @input=${(event: InputEvent) => {
                            this.manualLabel = (event.target as HTMLInputElement).value;
                        }}
                    />
                </div>
                <button
                    type="button"
                    class="pf-c-button pf-m-secondary"
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

        return html`<table class="pf-c-table pf-m-compact pf-m-grid-md" role="grid">
            <thead>
                <tr>
                    <th>
                        ${msg("Company", { id: "sources.oauth.dingtalk-allowlist.table.company" })}
                    </th>
                    <th>${msg("Mode", { id: "sources.oauth.dingtalk-allowlist.table.mode" })}</th>
                    <th>
                        ${msg("Department IDs", {
                            id: "sources.oauth.dingtalk-allowlist.table.departments",
                        })}
                    </th>
                    <th>
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
        return html`<tr>
            <td
                data-label=${msg("Company", {
                    id: "sources.oauth.dingtalk-allowlist.table.company",
                })}
            >
                <input
                    class="pf-c-form-control"
                    .value=${company.label}
                    aria-label=${msg("Company label", {
                        id: "sources.oauth.dingtalk-allowlist.company.label.aria-label",
                    })}
                    @input=${(event: InputEvent) => {
                        this.updateCompany(company.corpId, {
                            label: (event.target as HTMLInputElement).value,
                        });
                    }}
                />
                <div class="ak-dingtalk-muted">${company.corpId}</div>
            </td>
            <td data-label=${msg("Mode", { id: "sources.oauth.dingtalk-allowlist.table.mode" })}>
                <label>
                    <input
                        type="checkbox"
                        .checked=${company.allowAll}
                        @change=${(event: InputEvent) => {
                            this.updateCompany(company.corpId, {
                                allowAll: (event.target as HTMLInputElement).checked,
                            });
                        }}
                    />
                    ${msg("Allow full company", {
                        id: "sources.oauth.dingtalk-allowlist.company.allow-all",
                    })}
                </label>
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
                <div class="ak-dingtalk-input-row">
                    <input
                        class="pf-c-form-control ak-dingtalk-department-input"
                        ?disabled=${company.allowAll}
                        .value=${this.departmentInputs[company.corpId] ??
                        renderDingTalkDepartmentInput(company.deptIds.map(String))}
                        placeholder=${msg("IDs separated by commas or spaces", {
                            id: "sources.oauth.dingtalk-allowlist.departments.placeholder",
                        })}
                        @input=${(event: InputEvent) => {
                            this.departmentInputs = {
                                ...this.departmentInputs,
                                [company.corpId]: (event.target as HTMLInputElement).value,
                            };
                            this.markDirty();
                        }}
                    />
                    <button
                        type="button"
                        class="pf-c-button pf-m-secondary"
                        ?disabled=${company.allowAll}
                        @click=${() => this.addDepartments(company.corpId)}
                    >
                        ${msg("Add departments", {
                            id: "sources.oauth.dingtalk-allowlist.departments.add",
                        })}
                    </button>
                </div>
                ${this.renderDepartments(company)}
            </td>
            <td
                data-label=${msg("Actions", {
                    id: "sources.oauth.dingtalk-allowlist.table.actions",
                })}
            >
                <div class="ak-dingtalk-table-actions">
                    <ak-spinner-button
                        class="pf-m-secondary"
                        .callAction=${() => this.loadDepartments(company.corpId)}
                    >
                        ${msg("Load departments", {
                            id: "sources.oauth.dingtalk-allowlist.departments.load",
                        })}
                    </ak-spinner-button>
                    <button
                        type="button"
                        class="pf-c-button pf-m-danger"
                        @click=${() => this.removeCompany(company.corpId)}
                    >
                        ${msg("Remove", { id: "common.actions.remove" })}
                    </button>
                </div>
            </td>
        </tr>`;
    }

    private renderDepartments(company: DingTalkAllowlistModel["companies"][0]): TemplateResult {
        const departments = this.fetchedDepartments[company.corpId] || [];
        if (departments.length < 1) {
            return html``;
        }
        const selected = new Set(
            splitDingTalkDepartmentIds(
                this.departmentInputs[company.corpId] ??
                    renderDingTalkDepartmentInput(company.deptIds.map(String)),
            ),
        );
        const rows = buildDingTalkDepartmentTreeRows(departments, selected);
        return html`<table class="pf-c-table pf-m-compact pf-m-grid-md" role="grid">
            <caption>
                <div class="ak-dingtalk-department-actions">
                    <button
                        type="button"
                        class="pf-c-button pf-m-secondary pf-m-small"
                        ?disabled=${company.allowAll}
                        @click=${() => this.selectAllLoadedDepartments(company.corpId)}
                    >
                        ${msg("Select all", {
                            id: "sources.oauth.dingtalk-allowlist.departments.select-all",
                        })}
                    </button>
                    <button
                        type="button"
                        class="pf-c-button pf-m-secondary pf-m-small"
                        ?disabled=${company.allowAll}
                        @click=${() => this.invertLoadedDepartments(company.corpId)}
                    >
                        ${msg("Invert selection", {
                            id: "sources.oauth.dingtalk-allowlist.departments.invert",
                        })}
                    </button>
                </div>
            </caption>
            <thead>
                <tr>
                    <th>
                        ${msg("Allowed", {
                            id: "sources.oauth.dingtalk-allowlist.department.allowed",
                        })}
                    </th>
                    <th>
                        ${msg("Department ID", {
                            id: "sources.oauth.dingtalk-allowlist.department.id",
                        })}
                    </th>
                    <th>
                        ${msg("Name", { id: "sources.oauth.dingtalk-allowlist.department.name" })}
                    </th>
                    <th>
                        ${msg("Parent ID", {
                            id: "sources.oauth.dingtalk-allowlist.department.parent-id",
                        })}
                    </th>
                </tr>
            </thead>
            <tbody>
                ${rows.map(
                    (row) =>
                        html`<tr>
                            <td
                                data-label=${msg("Allowed", {
                                    id: "sources.oauth.dingtalk-allowlist.department.allowed",
                                })}
                            >
                                <input
                                    type="checkbox"
                                    ?disabled=${company.allowAll}
                                    .checked=${row.selection === "checked"}
                                    .indeterminate=${row.selection === "indeterminate"}
                                    @change=${(event: InputEvent) => {
                                        this.toggleLoadedDepartment(
                                            company.corpId,
                                            row.department.deptId,
                                            (event.target as HTMLInputElement).checked,
                                        );
                                    }}
                                />
                            </td>
                            <td
                                data-label=${msg("Department ID", {
                                    id: "sources.oauth.dingtalk-allowlist.department.id",
                                })}
                            >
                                <span
                                    class="ak-dingtalk-department-tree-cell"
                                    style=${`padding-inline-start: ${row.level * 1.5}rem;`}
                                >
                                    ${row.department.deptId}
                                </span>
                            </td>
                            <td
                                data-label=${msg("Name", {
                                    id: "sources.oauth.dingtalk-allowlist.department.name",
                                })}
                            >
                                ${row.department.name}
                            </td>
                            <td
                                data-label=${msg("Parent ID", {
                                    id: "sources.oauth.dingtalk-allowlist.department.parent-id",
                                })}
                            >
                                ${row.department.parentId || "-"}
                            </td>
                        </tr>`,
                )}
            </tbody>
        </table>`;
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

    private renderPartialFailures(): TemplateResult {
        if (this.partialFailures.length < 1) {
            return html``;
        }
        return html`<div class="pf-c-content ak-dingtalk-section">
            <strong
                >${msg("Partial failure details", {
                    id: "sources.oauth.dingtalk-allowlist.partial-failures.title",
                })}</strong
            >
            <ul>
                ${this.partialFailures.map((failure) => html`<li>${failure}</li>`)}
            </ul>
        </div>`;
    }

    private renderRemoveConfiguration(): TemplateResult {
        if (!this.policy) {
            return html``;
        }
        return html`<ak-forms-confirm
            successMessage=${msg("DingTalk allowlist policy and bindings removed.", {
                id: "sources.oauth.dingtalk-allowlist.remove.success",
            })}
            errorMessage=${msg("Failed to remove the DingTalk allowlist policy.", {
                id: "sources.oauth.dingtalk-allowlist.remove.error",
            })}
            action=${msg("Remove allowlist", {
                id: "sources.oauth.dingtalk-allowlist.remove.action",
            })}
            .onConfirm=${() => this.removeManagedConfiguration()}
        >
            <span slot="header"
                >${msg("Remove DingTalk allowlist", {
                    id: "sources.oauth.dingtalk-allowlist.remove.header",
                })}</span
            >
            <p slot="body">
                ${msg(
                    "This deletes the managed allowlist policy and all of its flow bindings. DingTalk logins will no longer be restricted by company or department.",
                    {
                        id: "sources.oauth.dingtalk-allowlist.remove.body",
                    },
                )}
            </p>
            <button slot="trigger" class="pf-c-button pf-m-danger pf-m-secondary" type="button">
                ${msg("Remove allowlist", {
                    id: "sources.oauth.dingtalk-allowlist.remove.action",
                })}
            </button>
            <div slot="modal"></div>
        </ak-forms-confirm>`;
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
                            .callAction=${() => this.discoverCompany()}
                        >
                            ${msg("Discover company", {
                                id: "sources.oauth.dingtalk-allowlist.discovery.start",
                            })}
                        </ak-spinner-button>
                        <ak-spinner-button
                            class="pf-m-primary"
                            .callAction=${() => this.saveAndApply()}
                        >
                            ${msg("Save and apply", {
                                id: "sources.oauth.dingtalk-allowlist.save.apply",
                            })}
                        </ak-spinner-button>
                        <ak-spinner-button
                            class="pf-m-secondary"
                            .callAction=${() => this.refreshStatus()}
                        >
                            ${msg("Refresh status", {
                                id: "sources.oauth.dingtalk-allowlist.status.refresh",
                            })}
                        </ak-spinner-button>
                        ${this.renderRemoveConfiguration()}
                    </div>
                    ${this.renderDiscoveryDetails()}
                    <ul class="ak-dingtalk-status ak-dingtalk-section">
                        ${this.statusItems().map((item) => this.renderStatus(item))}
                    </ul>
                    ${this.renderPartialFailures()} ${this.renderManualAdd()}
                    ${this.renderCompanies()}
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
