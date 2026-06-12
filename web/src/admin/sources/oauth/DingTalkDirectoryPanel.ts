import "#components/ak-status-label";
import "#elements/EmptyState";
import "#elements/buttons/SpinnerButton/index";

import { DEFAULT_CONFIG } from "#common/api/config";
import { MessageLevel } from "#common/messages";

import { AKElement } from "#elements/Base";
import { showMessage } from "#elements/messages/MessageContainer";
import { SlottedTemplateResult } from "#elements/types";

import { BaseAPI, OAuthSource } from "@goauthentik/api";

import { msg, str } from "@lit/localize";
import { css, CSSResult, html, PropertyValues, TemplateResult } from "lit";
import { customElement, property, state } from "lit/decorators.js";

import PFButton from "@patternfly/patternfly/components/Button/button.css";
import PFCard from "@patternfly/patternfly/components/Card/card.css";
import PFContent from "@patternfly/patternfly/components/Content/content.css";
import PFForm from "@patternfly/patternfly/components/Form/form.css";
import PFFormControl from "@patternfly/patternfly/components/FormControl/form-control.css";
import PFTable from "@patternfly/patternfly/components/Table/table.css";
import PFFlex from "@patternfly/patternfly/layouts/Flex/flex.css";

export interface DingTalkDirectorySyncStatus {
    corp_id: string;
    status: string;
    started_at: string | null;
    finished_at: string | null;
    error: string | null;
    counters: Record<string, unknown> | null;
}

interface DingTalkDirectoryStatusResponse {
    source_slug: string;
    sync: DingTalkDirectorySyncStatus[];
}

interface DingTalkDirectorySyncResponse {
    queued: boolean;
    corp_id: string;
}

export interface DingTalkDirectoryStatusSummary {
    total: number;
    success: number;
    error: number;
    running: number;
    unknown: number;
}

export interface DingTalkDirectorySummaryMetric {
    key: keyof DingTalkDirectoryStatusSummary;
    value: number;
    label: string;
}

export function dingtalkDirectoryStatusSummary(
    statuses: DingTalkDirectorySyncStatus[],
): DingTalkDirectoryStatusSummary {
    return statuses.reduce<DingTalkDirectoryStatusSummary>(
        (summary, status) => {
            summary.total += 1;
            switch (status.status) {
                case "success":
                    summary.success += 1;
                    break;
                case "error":
                    summary.error += 1;
                    break;
                case "running":
                    summary.running += 1;
                    break;
                default:
                    summary.unknown += 1;
                    break;
            }
            return summary;
        },
        {
            total: 0,
            success: 0,
            error: 0,
            running: 0,
            unknown: 0,
        },
    );
}

export function dingtalkDirectorySummaryMetrics(
    statuses: DingTalkDirectorySyncStatus[],
): DingTalkDirectorySummaryMetric[] {
    const summary = dingtalkDirectoryStatusSummary(statuses);
    const metrics: DingTalkDirectorySummaryMetric[] = [
        {
            key: "total",
            value: summary.total,
            label: msg("Corp sync records", {
                id: "sources.oauth.dingtalk-directory.summary.total.label",
            }),
        },
        {
            key: "success",
            value: summary.success,
            label: msg("Successful", {
                id: "sources.oauth.dingtalk-directory.summary.success.label",
            }),
        },
        {
            key: "error",
            value: summary.error,
            label: msg("Failed", {
                id: "sources.oauth.dingtalk-directory.summary.error.label",
            }),
        },
        {
            key: "running",
            value: summary.running,
            label: msg("Running", {
                id: "sources.oauth.dingtalk-directory.summary.running.label",
            }),
        },
    ];

    if (summary.unknown > 0) {
        metrics.push({
            key: "unknown",
            value: summary.unknown,
            label: msg("Unknown", {
                id: "sources.oauth.dingtalk-directory.summary.unknown.label",
            }),
        });
    }

    return metrics;
}

class DingTalkDirectoryApi extends BaseAPI {
    async status(sourceSlug: string): Promise<DingTalkDirectoryStatusResponse> {
        const response = await this.request({
            path: `/sources/oauth/dingtalk-directory/${encodeURIComponent(sourceSlug)}/status/`,
            method: "GET",
            headers: {},
            query: {},
        });
        return this.normalizeStatus(await response.json(), sourceSlug);
    }

    async sync(sourceSlug: string, corpId: string): Promise<DingTalkDirectorySyncResponse> {
        const response = await this.request({
            path: `/sources/oauth/dingtalk-directory/${encodeURIComponent(sourceSlug)}/sync/`,
            method: "POST",
            headers: { "Content-Type": "application/json" },
            query: {},
            body: { corp_id: corpId },
        });
        return this.normalizeSyncResponse(await response.json(), corpId);
    }

    private normalizeStatus(value: unknown, sourceSlug: string): DingTalkDirectoryStatusResponse {
        if (!value || typeof value !== "object") {
            return { source_slug: sourceSlug, sync: [] };
        }
        const record = value as Record<string, unknown>;
        const sync = Array.isArray(record.sync) ? record.sync : [];
        return {
            source_slug:
                this.normalizeString(record.source_slug ?? record.sourceSlug) || sourceSlug,
            sync: sync
                .map((status) => this.normalizeSyncStatus(status))
                .filter((status): status is DingTalkDirectorySyncStatus => status !== null),
        };
    }

    private normalizeSyncStatus(value: unknown): DingTalkDirectorySyncStatus | null {
        if (!value || typeof value !== "object") {
            return null;
        }
        const record = value as Record<string, unknown>;
        const corpId = this.normalizeString(record.corp_id ?? record.corpId);
        if (!corpId) {
            return null;
        }
        return {
            corp_id: corpId,
            status: this.normalizeString(record.status) || "unknown",
            started_at: this.normalizeNullableString(record.started_at ?? record.startedAt),
            finished_at: this.normalizeNullableString(record.finished_at ?? record.finishedAt),
            error: this.normalizeNullableString(record.error),
            counters:
                record.counters && typeof record.counters === "object"
                    ? (record.counters as Record<string, unknown>)
                    : {},
        };
    }

    private normalizeSyncResponse(value: unknown, corpId: string): DingTalkDirectorySyncResponse {
        if (!value || typeof value !== "object") {
            return { queued: false, corp_id: corpId };
        }
        const record = value as Record<string, unknown>;
        return {
            queued: record.queued === true,
            corp_id: this.normalizeString(record.corp_id ?? record.corpId) || corpId,
        };
    }

    private normalizeNullableString(value: unknown): string | null {
        const normalized = this.normalizeString(value);
        return normalized || null;
    }

    private normalizeString(value: unknown): string {
        if (value === undefined || value === null) {
            return "";
        }
        return String(value).trim();
    }
}

@customElement("ak-source-oauth-dingtalk-directory")
export class DingTalkDirectoryPanel extends AKElement {
    @property({ attribute: false })
    public source?: OAuthSource;

    @state()
    private statuses: DingTalkDirectorySyncStatus[] = [];

    @state()
    private manualCorpId = "";

    @state()
    private loaded = false;

    private api = new DingTalkDirectoryApi(DEFAULT_CONFIG);

    static styles: CSSResult[] = [
        PFButton,
        PFCard,
        PFContent,
        PFForm,
        PFFormControl,
        PFTable,
        PFFlex,
        css`
            .ak-dingtalk-directory-actions {
                align-items: flex-end;
                gap: var(--pf-global--spacer--sm);
            }

            .ak-dingtalk-directory-actions .pf-c-form__group {
                min-width: 16rem;
            }

            .ak-dingtalk-directory-summary {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(8rem, 1fr));
                gap: var(--pf-global--spacer--sm);
                margin: 0;
                padding: 0;
                list-style: none;
            }

            .ak-dingtalk-directory-summary-item {
                min-width: 0;
                padding: var(--pf-global--spacer--sm);
                border: 1px solid var(--pf-global--BorderColor--100);
                border-radius: 4px;
            }

            .ak-dingtalk-directory-summary-value,
            .ak-dingtalk-directory-summary-label {
                display: block;
                overflow-wrap: anywhere;
            }

            .ak-dingtalk-directory-summary-value {
                font-size: var(--pf-global--FontSize--xl);
                font-weight: var(--pf-global--FontWeight--bold);
                line-height: 1.2;
            }

            .ak-dingtalk-directory-summary-label {
                color: var(--pf-global--Color--200);
                font-size: var(--pf-global--FontSize--sm);
            }

            .ak-dingtalk-directory-muted {
                color: var(--pf-global--Color--200);
            }

            .ak-dingtalk-directory-error {
                color: var(--pf-global--danger-color--100);
            }

            .ak-dingtalk-directory-counters {
                margin: 0;
                padding: 0;
                list-style: none;
            }
        `,
    ];

    protected willUpdate(changedProperties: PropertyValues<this>): void {
        if (changedProperties.has("source") && this.source?.slug) {
            this.refreshStatus();
        }
    }

    private async refreshStatus(): Promise<void> {
        if (!this.source?.slug) {
            return;
        }
        const response = await this.api.status(this.source.slug);
        this.statuses = response.sync;
        this.loaded = true;
    }

    private async triggerManualSync(): Promise<void> {
        if (!this.source?.slug) {
            return;
        }
        const corpId = this.manualCorpId.trim();
        if (!corpId) {
            throw new Error(
                msg("Corp ID is required to queue a DingTalk directory sync.", {
                    id: "sources.oauth.dingtalk-directory.sync.corp-id-required",
                }),
            );
        }
        const response = await this.api.sync(this.source.slug, corpId);
        if (response.queued) {
            showMessage({
                level: MessageLevel.success,
                message: msg(str`Queued DingTalk directory sync for ${response.corp_id}`, {
                    id: "sources.oauth.dingtalk-directory.sync.queued",
                }),
            });
        }
        this.manualCorpId = "";
        await this.refreshStatus();
    }

    private renderStatusLabel(status: string): TemplateResult {
        switch (status) {
            case "success":
                return html`<ak-status-label
                    good
                    good-label=${msg("Success", {
                        id: "sources.oauth.dingtalk-directory.status.success",
                    })}
                ></ak-status-label>`;
            case "error":
                return html`<ak-status-label
                    type="error"
                    bad-label=${msg("Error", {
                        id: "sources.oauth.dingtalk-directory.status.error",
                    })}
                ></ak-status-label>`;
            case "running":
                return html`<ak-status-label
                    type="warning"
                    bad-label=${msg("Running", {
                        id: "sources.oauth.dingtalk-directory.status.running",
                    })}
                ></ak-status-label>`;
            default:
                return html`<ak-status-label
                    type="info"
                    bad-label=${status ||
                    msg("Unknown", {
                        id: "sources.oauth.dingtalk-directory.status.unknown",
                    })}
                ></ak-status-label>`;
        }
    }

    private renderTimestamp(value: string | null): string {
        if (!value) {
            return msg("-", { id: "sources.oauth.dingtalk-directory.timestamp.empty" });
        }
        const parsed = new Date(value);
        if (Number.isNaN(parsed.valueOf())) {
            return value;
        }
        return parsed.toLocaleString();
    }

    private renderCounters(counters: Record<string, unknown> | null): TemplateResult | string {
        const entries = Object.entries(counters ?? {});
        if (entries.length < 1) {
            return msg("-", { id: "sources.oauth.dingtalk-directory.counters.empty" });
        }
        return html`<ul class="ak-dingtalk-directory-counters">
            ${entries.map(
                ([key, value]) =>
                    html`<li>
                        <span>${key}</span>:
                        <span
                            >${typeof value === "object"
                                ? JSON.stringify(value)
                                : String(value)}</span
                        >
                    </li>`,
            )}
        </ul>`;
    }

    private renderSummary(): TemplateResult {
        return html`<ul class="ak-dingtalk-directory-summary">
            ${dingtalkDirectorySummaryMetrics(this.statuses).map(
                (metric) =>
                    html`<li class="ak-dingtalk-directory-summary-item">
                        <span class="ak-dingtalk-directory-summary-value">${metric.value}</span>
                        <span class="ak-dingtalk-directory-summary-label">${metric.label}</span>
                    </li>`,
            )}
        </ul>`;
    }

    private renderActions(): TemplateResult {
        return html`<div class="pf-l-flex ak-dingtalk-directory-actions">
            <ak-spinner-button class="pf-m-secondary" .callAction=${() => this.refreshStatus()}>
                ${msg("Refresh", { id: "sources.oauth.dingtalk-directory.refresh" })}
            </ak-spinner-button>
            <div class="pf-c-form__group">
                <label class="pf-c-form__label">
                    <span class="pf-c-form__label-text"
                        >${msg("Corp ID", {
                            id: "sources.oauth.dingtalk-directory.corp-id",
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
            <ak-spinner-button class="pf-m-primary" .callAction=${() => this.triggerManualSync()}>
                ${msg("Sync now", { id: "sources.oauth.dingtalk-directory.sync-now" })}
            </ak-spinner-button>
        </div>`;
    }

    private renderTable(): TemplateResult {
        if (!this.loaded) {
            return html`<ak-empty-state loading></ak-empty-state>`;
        }
        if (this.statuses.length < 1) {
            return html`<ak-empty-state icon="pf-icon-sync">
                <span
                    >${msg("No DingTalk directory sync status yet.", {
                        id: "sources.oauth.dingtalk-directory.empty.title",
                    })}</span
                >
                <div slot="body">
                    ${msg("Enter a corp ID and start a manual sync to populate directory status.", {
                        id: "sources.oauth.dingtalk-directory.empty.body",
                    })}
                </div>
            </ak-empty-state>`;
        }
        return html`<table class="pf-c-table pf-m-compact pf-m-grid-md" role="grid">
            <thead>
                <tr>
                    <th>
                        ${msg("Corp ID", { id: "sources.oauth.dingtalk-directory.table.corp-id" })}
                    </th>
                    <th>
                        ${msg("Status", { id: "sources.oauth.dingtalk-directory.table.status" })}
                    </th>
                    <th>
                        ${msg("Started", { id: "sources.oauth.dingtalk-directory.table.started" })}
                    </th>
                    <th>
                        ${msg("Finished", {
                            id: "sources.oauth.dingtalk-directory.table.finished",
                        })}
                    </th>
                    <th>
                        ${msg("Counters", {
                            id: "sources.oauth.dingtalk-directory.table.counters",
                        })}
                    </th>
                    <th>${msg("Error", { id: "sources.oauth.dingtalk-directory.table.error" })}</th>
                </tr>
            </thead>
            <tbody>
                ${this.statuses.map(
                    (status) =>
                        html`<tr>
                            <td
                                data-label=${msg("Corp ID", {
                                    id: "sources.oauth.dingtalk-directory.table.corp-id",
                                })}
                            >
                                ${status.corp_id}
                            </td>
                            <td
                                data-label=${msg("Status", {
                                    id: "sources.oauth.dingtalk-directory.table.status",
                                })}
                            >
                                ${this.renderStatusLabel(status.status)}
                            </td>
                            <td
                                data-label=${msg("Started", {
                                    id: "sources.oauth.dingtalk-directory.table.started",
                                })}
                            >
                                ${this.renderTimestamp(status.started_at)}
                            </td>
                            <td
                                data-label=${msg("Finished", {
                                    id: "sources.oauth.dingtalk-directory.table.finished",
                                })}
                            >
                                ${this.renderTimestamp(status.finished_at)}
                            </td>
                            <td
                                data-label=${msg("Counters", {
                                    id: "sources.oauth.dingtalk-directory.table.counters",
                                })}
                            >
                                ${this.renderCounters(status.counters)}
                            </td>
                            <td
                                data-label=${msg("Error", {
                                    id: "sources.oauth.dingtalk-directory.table.error",
                                })}
                            >
                                ${status.error
                                    ? html`<span class="ak-dingtalk-directory-error"
                                          >${status.error}</span
                                      >`
                                    : html`<span class="ak-dingtalk-directory-muted"
                                          >${msg("-", {
                                              id: "sources.oauth.dingtalk-directory.error.empty",
                                          })}</span
                                      >`}
                            </td>
                        </tr>`,
                )}
            </tbody>
        </table>`;
    }

    render(): SlottedTemplateResult {
        return html`<div class="pf-c-card">
            <div class="pf-c-card__title">
                ${msg("DingTalk directory sync", {
                    id: "sources.oauth.dingtalk-directory.title",
                })}
            </div>
            <div class="pf-c-card__body pf-c-content">
                ${this.renderSummary()}
                <p>
                    <a
                        href="https://docs.goauthentik.io/docs/sources/dingtalk/"
                        target="_blank"
                        rel="noopener noreferrer"
                    >
                        ${msg("Open DingTalk source documentation", {
                            id: "sources.oauth.dingtalk-directory.docs",
                        })}
                    </a>
                </p>
            </div>
            <div class="pf-c-card__body pf-c-form">${this.renderActions()}</div>
            <div class="pf-c-card__body">${this.renderTable()}</div>
        </div>`;
    }
}

declare global {
    interface HTMLElementTagNameMap {
        "ak-source-oauth-dingtalk-directory": DingTalkDirectoryPanel;
    }
}
