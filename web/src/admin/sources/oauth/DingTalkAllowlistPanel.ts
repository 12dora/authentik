import "#components/ak-status-label";
import "#elements/buttons/SpinnerButton/index";
import "#elements/EmptyState";

import { DEFAULT_CONFIG } from "#common/api/config";
import { parseAPIResponseError, pluckErrorDetail } from "#common/errors/network";
import { MessageLevel } from "#common/messages";

import { AKElement } from "#elements/Base";
import { modalInvoker } from "#elements/dialogs";
import { showMessage } from "#elements/messages/MessageContainer";
import { SlottedTemplateResult } from "#elements/types";

import { ExpressionPolicyForm } from "#admin/policies/expression/ExpressionPolicyForm";
import type { StatusItem, StatusState } from "#admin/sources/oauth/DingTalkAllowlistPanelState";
import {
    addDingTalkDepartments,
    applyDingTalkDepartmentInputs,
    buildDingTalkDepartmentTreeRows,
    dingtalkDepartmentFetchFailureStatus,
    dingtalkDepartmentInputsFromModel,
    dingtalkStatusLabelProperties,
    invertLoadedDingTalkDepartmentInput,
    mergeLoadedDingTalkDepartmentInput,
    removeDingTalkCompany,
    renderDingTalkDepartmentInput,
    saveDingTalkAllowlistConfiguration,
    selectLoadedDingTalkDepartmentInput,
    singleDingTalkLoginEntryStatusItem,
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
    BaseAPI,
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

import PFButton from "@patternfly/patternfly/components/Button/button.css";
import PFCard from "@patternfly/patternfly/components/Card/card.css";
import PFContent from "@patternfly/patternfly/components/Content/content.css";
import PFForm from "@patternfly/patternfly/components/Form/form.css";
import PFFormControl from "@patternfly/patternfly/components/FormControl/form-control.css";
import PFTable from "@patternfly/patternfly/components/Table/table.css";
import PFFlex from "@patternfly/patternfly/layouts/Flex/flex.css";

interface DingTalkDiscoveryStart {
    url: string;
}

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

interface DingTalkDepartmentResponse {
    corpId: string;
    label?: string;
    departments: DingTalkDepartment[];
}

interface DingTalkStatusResponse {
    config?: unknown;
    sourceLinkGuard?: StatusState | boolean;
    sourceLinkGuardDetail?: string;
    expressionValid?: boolean | null;
}

class DingTalkAllowlistDiscoveryApi extends BaseAPI {
    async status(sourceSlug: string): Promise<DingTalkStatusResponse> {
        const response = await this.request({
            path: `/sources/oauth/dingtalk-allowlist/${encodeURIComponent(sourceSlug)}/status/`,
            method: "GET",
            headers: {},
            query: {},
        });
        return this.normalizeStatus(await response.json());
    }

    async discoverStart(sourceSlug: string): Promise<DingTalkDiscoveryStart> {
        const response = await this.request({
            path: `/sources/oauth/dingtalk-allowlist/${encodeURIComponent(
                sourceSlug,
            )}/discover/start/`,
            method: "POST",
            headers: { "Content-Type": "application/json" },
            query: {},
            body: {},
        });
        return this.normalizeDiscoveryStart(await response.json());
    }

    async departments(sourceSlug: string, corpId: string): Promise<DingTalkDepartmentResponse> {
        const response = await this.request({
            path: `/sources/oauth/dingtalk-allowlist/${encodeURIComponent(
                sourceSlug,
            )}/departments/`,
            method: "POST",
            headers: { "Content-Type": "application/json" },
            query: {},
            body: { corp_id: corpId },
        });
        return this.normalizeDepartmentResponse(await response.json(), corpId);
    }

    private normalizeStatus(value: unknown): DingTalkStatusResponse {
        if (!value || typeof value !== "object") {
            return {};
        }
        const record = value as Record<string, unknown>;
        const guard =
            record.source_link_guard ??
            record.sourceLinkGuard ??
            (record.status && typeof record.status === "object"
                ? (record.status as Record<string, unknown>).source_link_guard
                : undefined);
        const expressionValid = record.expression_valid ?? record.expressionValid;

        return {
            config: record.config,
            sourceLinkGuard: this.normalizeStatusValue(guard),
            sourceLinkGuardDetail: this.normalizeOptionalString(
                record.source_link_guard_detail ?? record.sourceLinkGuardDetail,
            ),
            expressionValid:
                typeof expressionValid === "boolean" || expressionValid === null
                    ? expressionValid
                    : undefined,
        };
    }

    private normalizeStatusValue(value: unknown): StatusState | boolean | undefined {
        if (value && typeof value === "object") {
            const record = value as Record<string, unknown>;
            return this.normalizeStatusValue(record.enabled ?? record.state ?? record.status);
        }
        if (typeof value === "boolean") {
            return value;
        }
        if (typeof value === "string") {
            const normalized = value.toLowerCase();
            if (["good", "warning", "danger", "unknown"].includes(normalized)) {
                return normalized as StatusState;
            }
            if (["installed", "disabled", "ok", "true"].includes(normalized)) {
                return true;
            }
            if (["missing", "failed", "false"].includes(normalized)) {
                return false;
            }
        }
        return undefined;
    }

    private normalizeDiscoveryStart(value: unknown): DingTalkDiscoveryStart {
        if (!value || typeof value !== "object") {
            throw new Error(
                msg("DingTalk discovery did not return a start URL.", {
                    id: "sources.oauth.dingtalk-allowlist.discovery.missing-start-url",
                }),
            );
        }
        const record = value as Record<string, unknown>;
        const url = this.normalizeOptionalString(
            record.url ?? record.authorization_url ?? record.redirect_url,
        );
        if (!url) {
            throw new Error(
                msg("DingTalk discovery did not return a start URL.", {
                    id: "sources.oauth.dingtalk-allowlist.discovery.missing-start-url",
                }),
            );
        }
        return { url };
    }

    private normalizeDepartmentResponse(
        value: unknown,
        requestedCorpId: string,
    ): DingTalkDepartmentResponse {
        if (!value || typeof value !== "object") {
            return { corpId: requestedCorpId, departments: [] };
        }

        const record = value as Record<string, unknown>;
        const departments = Array.isArray(record.departments) ? record.departments : [];
        return {
            corpId:
                this.normalizeOptionalString(record.corp_id ?? record.corpId) || requestedCorpId,
            label: this.normalizeOptionalString(
                record.label ??
                    record.company ??
                    record.company_name ??
                    record.companyName ??
                    record.corp_name ??
                    record.corpName,
            ),
            departments: departments
                .map((department) => this.normalizeDepartment(department))
                .filter((department): department is DingTalkDepartment => department !== null),
        };
    }

    private normalizeDepartment(value: unknown): DingTalkDepartment | null {
        if (!value || typeof value !== "object") {
            return null;
        }
        const record = value as Record<string, unknown>;
        const deptId = this.normalizeOptionalString(record.dept_id ?? record.deptId);
        if (!deptId) {
            return null;
        }
        return {
            deptId,
            name: this.normalizeOptionalString(record.name) || deptId,
            parentId: this.normalizeOptionalString(record.parent_id ?? record.parentId) || null,
        };
    }

    private normalizeOptionalString(value: unknown): string {
        if (value === undefined || value === null) {
            return "";
        }
        return String(value).trim();
    }
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

    private policiesApi = new PoliciesApi(DEFAULT_CONFIG);
    private flowsApi = new FlowsApi(DEFAULT_CONFIG);
    private sourcesApi = new SourcesApi(DEFAULT_CONFIG);
    private discoveryApi = new DingTalkAllowlistDiscoveryApi(DEFAULT_CONFIG);

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
            this.refreshStatus();
        }
    }

    private get policyName(): string {
        return `dingtalk-allowlist-${this.source?.slug || ""}`;
    }

    private handleDiscoveryMessage = (event: MessageEvent<unknown>): void => {
        if (event.origin && event.origin !== window.location.origin) {
            return;
        }
        const result = this.extractDiscoveryResult(event.data);
        if (!result) {
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

    private extractDiscoveryResult(value: unknown): DingTalkDiscoveryResult | null {
        if (!value || typeof value !== "object") {
            return null;
        }
        const record = value as Record<string, unknown>;
        if (record.source && record.source !== "goauthentik.io" && record.source !== "authentik") {
            return null;
        }
        if (
            record.context &&
            record.context !== "dingtalk-allowlist-discovery" &&
            record.context !== "sources.oauth.dingtalk-allowlist.discovery"
        ) {
            return null;
        }
        const payload =
            record.result && typeof record.result === "object"
                ? (record.result as Record<string, unknown>)
                : record;
        const corpId = this.normalizeString(payload.corpId ?? payload.corp_id);
        if (!corpId) {
            return null;
        }
        return {
            corpId,
            label: this.normalizeString(
                payload.label ??
                    payload.company ??
                    payload.company_name ??
                    payload.companyName ??
                    payload.corp_name ??
                    payload.corpName,
            ),
            userId: this.normalizeString(payload.userId ?? payload.userid ?? payload.user_id),
        };
    }

    private normalizeString(value: unknown): string {
        if (value === undefined || value === null) {
            return "";
        }
        return String(value).trim();
    }

    private async refreshStatus(): Promise<void> {
        if (!this.source?.slug) {
            return;
        }

        this.partialFailures = [];
        await this.refreshManagedPolicy();
        await this.refreshFlowsAndBindings();

        try {
            const status = await this.discoveryApi.status(this.source.slug);
            this.applyBackendStatus(status);
        } catch (error) {
            this.sourceLinkGuard = {
                label: this.sourceLinkGuard.label,
                state: "unknown",
                detail: msg("Discovery status endpoint unavailable", {
                    id: "sources.oauth.dingtalk-allowlist.status.endpoint-unavailable",
                }),
            };
            this.partialFailures = [...this.partialFailures, this.errorMessage(error)];
        }
    }

    private async refreshManagedPolicy(): Promise<void> {
        const response = await this.policiesApi.policiesExpressionList({
            name: this.policyName,
            pageSize: 1,
        });
        this.policy = response.results.find((policy) => policy.name === this.policyName);
        if (!this.policy) {
            this.expressionValid = undefined;
            return;
        }
        const parsed = parseDingTalkAllowlistPolicy(this.policy.expression);
        if (parsed) {
            this.model = parsed;
            this.departmentInputs = dingtalkDepartmentInputsFromModel(parsed);
            this.expressionValid = true;
        } else {
            this.expressionValid = false;
        }
    }

    private async refreshFlowsAndBindings(): Promise<void> {
        this.authFlow = await this.resolveFlow(this.source?.authenticationFlow);
        this.enrollmentFlow = await this.resolveFlow(this.source?.enrollmentFlow);

        const [authBinding, enrollmentBinding] = await Promise.all([
            this.findPolicyBinding(this.authFlow?.policybindingmodelPtrId, this.policy?.pk),
            this.findPolicyBinding(this.enrollmentFlow?.policybindingmodelPtrId, this.policy?.pk),
        ]);

        this.authBinding = authBinding;
        this.enrollmentBinding = enrollmentBinding;
    }

    private applyBackendStatus(status: DingTalkStatusResponse): void {
        const config = status.config as { companies?: unknown } | undefined;
        if (config && Array.isArray(config.companies) && config.companies.length > 0) {
            try {
                const model = dingTalkAllowlistModelFromStoredConfig(config);
                if (model) {
                    this.model = model;
                    this.departmentInputs = dingtalkDepartmentInputsFromModel(model);
                }
            } catch (error) {
                this.partialFailures = [...this.partialFailures, this.errorMessage(error)];
            }
        }

        if (status.expressionValid !== undefined) {
            this.expressionValid = status.expressionValid;
        }

        const guard = status.sourceLinkGuard;
        if (guard === undefined) {
            return;
        }

        const state = typeof guard === "boolean" ? (guard ? "good" : "danger") : guard;
        this.sourceLinkGuard = {
            label: this.sourceLinkGuard.label,
            state,
            detail:
                status.sourceLinkGuardDetail ||
                (state === "good"
                    ? msg("Installed or disabled", {
                          id: "sources.oauth.dingtalk-allowlist.status.source-link.good",
                      })
                    : msg("Needs review", {
                          id: "sources.oauth.dingtalk-allowlist.status.source-link.review",
                      })),
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
        this.model = upsertDingTalkCompany(this.model, corpId, label, allowAll, deptIds);
        this.departmentInputs = {
            ...this.departmentInputs,
            [corpId]: allowAll ? "" : renderDingTalkDepartmentInput(deptIds),
        };
    }

    private addManualCompany(): void {
        const corpId = this.normalizeString(this.manualCorpId);
        if (!corpId) {
            showMessage({
                level: MessageLevel.error,
                message: msg("Company corpId is required.", {
                    id: "sources.oauth.dingtalk-allowlist.validation.corp-id-required",
                }),
            });
            return;
        }
        this.upsertCompany(corpId, this.normalizeString(this.manualLabel) || corpId, true, []);
        this.manualCorpId = "";
        this.manualLabel = "";
    }

    private updateCompany(corpId: string, patch: Partial<DingTalkAllowlistModel["companies"][0]>) {
        this.model = updateDingTalkCompany(this.model, corpId, patch);
    }

    private removeCompany(corpId: string): void {
        this.model = removeDingTalkCompany(this.model, corpId);
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
        const start = await this.discoveryApi.discoverStart(this.source.slug);
        window.open(start.url, "authentik-dingtalk-discovery", "popup,width=640,height=760");
    }

    private async loadDepartments(corpId: string): Promise<void> {
        if (!this.source?.slug) {
            return;
        }
        try {
            const response = await this.discoveryApi.departments(this.source.slug, corpId);
            const previousLoadedDeptIds = (this.fetchedDepartments[corpId] || []).map(
                (department) => department.deptId,
            );
            const loadedDeptIds = response.departments.map((department) => department.deptId);
            const departmentInput = mergeLoadedDingTalkDepartmentInput(
                this.departmentInputs[corpId] ||
                    renderDingTalkDepartmentInput(
                        this.model.companies
                            .find((company) => company.corpId === corpId)
                            ?.deptIds.map(String) || [],
                    ),
                previousLoadedDeptIds,
                loadedDeptIds,
            );
            this.fetchedDepartments = {
                ...this.fetchedDepartments,
                [corpId]: response.departments,
            };
            this.departmentInputs = {
                ...this.departmentInputs,
                [corpId]: departmentInput,
            };
            this.updateCompany(corpId, {
                label:
                    response.label ||
                    this.model.companies.find((company) => company.corpId === corpId)?.label ||
                    corpId,
                allowAll: false,
                deptIds: departmentInput ? departmentInput.split(/\s+/u) : [],
            });
            this.lastDepartmentFetch = {
                label: this.lastDepartmentFetch.label,
                state: "good",
                detail: msg(str`${response.departments.length} departments loaded for ${corpId}`, {
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

        const existing = await this.policiesApi.policiesBindingsList({
            target: flow.policybindingmodelPtrId,
            policy: policy.pk,
            pageSize: 20,
        });
        const current = existing.results.find((binding) => binding.policy === policy.pk);
        if (current) {
            await this.policiesApi.policiesBindingsPartialUpdate({
                policyBindingUuid: current.pk,
                patchedPolicyBindingRequest: {
                    enabled: true,
                    timeout: current.timeout ?? 30,
                    failureResult: false,
                    order: current.order,
                },
            });
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
            singleDingTalkLoginEntryStatusItem(
                msg("One visible DingTalk login entry is expected", {
                    id: "sources.oauth.dingtalk-allowlist.status.single-visible-entry",
                }),
            ),
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
            <ak-status-label type=${type} ?good=${good}></ak-status-label>
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
                        value=${this.manualCorpId}
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
                        value=${this.manualLabel}
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
                        ?checked=${company.allowAll}
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
                        .value=${this.departmentInputs[company.corpId] ?? company.deptIds.join(" ")}
                        placeholder=${msg("IDs separated by commas or spaces", {
                            id: "sources.oauth.dingtalk-allowlist.departments.placeholder",
                        })}
                        @input=${(event: InputEvent) => {
                            this.departmentInputs = {
                                ...this.departmentInputs,
                                [company.corpId]: (event.target as HTMLInputElement).value,
                            };
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
                                    ?checked=${row.selection === "checked"}
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
