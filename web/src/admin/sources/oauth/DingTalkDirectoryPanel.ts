import "#components/ak-status-label";
import "#elements/EmptyState";
import "#elements/buttons/SpinnerButton/index";
import "#elements/forms/ConfirmationForm";
import "#elements/tasks/ScheduleList";

import { DEFAULT_CONFIG } from "#common/api/config";
import { parseAPIResponseError, pluckErrorDetail } from "#common/errors/network";
import { MessageLevel } from "#common/messages";

import { AKElement } from "#elements/Base";
import { showMessage } from "#elements/messages/MessageContainer";
import { SlottedTemplateResult } from "#elements/types";

import { DingTalkDirectorySyncStatus, OAuthSource, SourcesApi } from "@goauthentik/api";

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

export type { DingTalkDirectorySyncStatus };

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

class DingTalkDirectoryApi extends SourcesApi {
    // The vendored client does not expose a destroy method for this endpoint, so
    // this is the one hand-written call. corp_id travels as a query parameter:
    // request bodies on DELETE are stripped by some proxies.
    async sourcesOauthDingtalkDirectorySyncDestroy(
        sourceSlug: string,
        corpId: string,
    ): Promise<void> {
        await this.request({
            path: `/sources/oauth/dingtalk-directory/${encodeURIComponent(sourceSlug)}/sync/`,
            method: "DELETE",
            headers: {},
            query: { corp_id: corpId },
        });
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

    @state()
    private loadError?: string;

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
                grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr));
                gap: var(--pf-global--spacer--sm);
                margin: 0;
                padding: 0;
                list-style: none;
            }

            .ak-dingtalk-directory-summary-item {
                display: grid;
                row-gap: var(--pf-global--spacer--xs);
                align-content: start;
                min-width: 0;
                padding: var(--pf-global--spacer--sm);
                border: 1px solid var(--pf-global--BorderColor--100);
                border-radius: 4px;
                line-height: 1.35;
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
            this.refreshStatus().catch(console.error);
        }
    }

    private async refreshStatus(): Promise<void> {
        if (!this.source?.slug) {
            return;
        }
        try {
            const response = await this.api.sourcesOauthDingtalkDirectoryStatusRetrieve({
                sourceSlug: this.source.slug,
            });
            this.statuses = response.sync;
            this.loadError = undefined;
        } catch (error) {
            this.loadError = pluckErrorDetail(await parseAPIResponseError(error));
        } finally {
            this.loaded = true;
        }
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
        const response = await this.api.sourcesOauthDingtalkDirectorySyncCreate({
            sourceSlug: this.source.slug,
            dingTalkDirectorySyncRequestRequest: { corpId },
        });
        if (response.queued) {
            showMessage({
                level: MessageLevel.success,
                message: msg(str`Queued DingTalk directory sync for ${response.corpId}`, {
                    id: "sources.oauth.dingtalk-directory.sync.queued",
                }),
            });
        }
        this.manualCorpId = "";
        await this.refreshStatus();
    }

    private async deleteSyncStatus(corpId: string): Promise<void> {
        if (!this.source?.slug) {
            return;
        }
        await this.api.sourcesOauthDingtalkDirectorySyncDestroy(this.source.slug, corpId);
        await this.refreshStatus();
    }

    private renderStatusLabel(status: string | undefined): TemplateResult {
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

    private renderTimestamp(value: Date | null | undefined): string {
        if (!value || Number.isNaN(value.valueOf())) {
            return msg("-", { id: "sources.oauth.dingtalk-directory.timestamp.empty" });
        }
        return value.toLocaleString();
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

    private renderDeleteAction(status: DingTalkDirectorySyncStatus): TemplateResult {
        return html`<ak-forms-confirm
            successMessage=${msg(str`Deleted DingTalk directory sync record for ${status.corpId}`, {
                id: "sources.oauth.dingtalk-directory.delete.success",
            })}
            errorMessage=${msg("Failed to delete DingTalk directory data.", {
                id: "sources.oauth.dingtalk-directory.delete.error",
            })}
            action=${msg("Delete", {
                id: "sources.oauth.dingtalk-directory.delete",
            })}
            .onConfirm=${() => this.deleteSyncStatus(status.corpId)}
        >
            <span slot="header"
                >${msg("Delete DingTalk directory data", {
                    id: "sources.oauth.dingtalk-directory.delete.header",
                })}</span
            >
            <p slot="body">
                ${msg(
                    str`This deletes the sync record and all cached departments and users for ${status.corpId}. Lookups that rely on the cached directory will return no data until the next sync completes.`,
                    {
                        id: "sources.oauth.dingtalk-directory.delete.body",
                    },
                )}
            </p>
            <button slot="trigger" class="pf-c-button pf-m-danger pf-m-secondary" type="button">
                ${msg("Delete", {
                    id: "sources.oauth.dingtalk-directory.delete",
                })}
            </button>
            <div slot="modal"></div>
        </ak-forms-confirm>`;
    }

    private renderTable(): TemplateResult {
        if (!this.loaded) {
            return html`<ak-empty-state loading></ak-empty-state>`;
        }
        if (this.loadError) {
            return html`<ak-empty-state icon="fa-exclamation-triangle">
                <span
                    >${msg("Failed to load DingTalk directory status.", {
                        id: "sources.oauth.dingtalk-directory.error.load",
                    })}</span
                >
                <div slot="body">
                    <span class="ak-dingtalk-directory-error">${this.loadError}</span>
                    <p>
                        ${msg("Use Refresh to try again.", {
                            id: "sources.oauth.dingtalk-directory.error.retry",
                        })}
                    </p>
                </div>
            </ak-empty-state>`;
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
                    <th>
                        ${msg("Actions", {
                            id: "sources.oauth.dingtalk-directory.table.actions",
                        })}
                    </th>
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
                                ${status.corpId}
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
                                ${this.renderTimestamp(status.startedAt)}
                            </td>
                            <td
                                data-label=${msg("Finished", {
                                    id: "sources.oauth.dingtalk-directory.table.finished",
                                })}
                            >
                                ${this.renderTimestamp(status.finishedAt)}
                            </td>
                            <td
                                data-label=${msg("Counters", {
                                    id: "sources.oauth.dingtalk-directory.table.counters",
                                })}
                            >
                                ${this.renderCounters(
                                    (status.counters ?? null) as Record<string, unknown> | null,
                                )}
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
                            <td
                                data-label=${msg("Actions", {
                                    id: "sources.oauth.dingtalk-directory.table.actions",
                                })}
                            >
                                ${this.renderDeleteAction(status)}
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
            <div class="pf-c-card__title">
                ${msg("Schedules", {
                    id: "sources.oauth.dingtalk-directory.schedules.title",
                })}
            </div>
            <div class="pf-c-card__body">
                <ak-schedule-list
                    .actorName=${"authentik.sources.oauth.tasks.dingtalk_directory_sync_all"}
                ></ak-schedule-list>
            </div>
        </div>`;
    }
}

declare global {
    interface HTMLElementTagNameMap {
        "ak-source-oauth-dingtalk-directory": DingTalkDirectoryPanel;
    }
}
