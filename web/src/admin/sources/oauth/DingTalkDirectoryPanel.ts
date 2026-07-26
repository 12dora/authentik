import "#components/ak-status-label";
import "#elements/EmptyState";
import "#elements/buttons/SpinnerButton/index";
import "#elements/tasks/ScheduleList";
import "#elements/timestamp/ak-timestamp";

import { confirmDingTalkDestructiveAction } from "./DingTalkDestructiveActionModal";
import { DingTalkDirectoryClient, GeneratedDingTalkDirectoryClient } from "./DingTalkDirectoryApi";
import {
    canDeleteDingTalkDirectoryStatus,
    dingtalkDirectoryStatusSummary,
    DingTalkDirectoryStatusSummary,
    dingtalkDirectorySummaryMetrics,
    DingTalkDirectorySyncStatus,
    dingtalkDirectoryTerminalEvents,
    hasRunningDingTalkDirectorySync,
    nextDingTalkDirectoryPollDelay,
    summarizeDingTalkDirectoryError,
} from "./DingTalkDirectoryPanelController";

import { parseAPIResponseError, pluckErrorDetail } from "#common/errors/network";
import { MessageLevel } from "#common/messages";

import { AKElement } from "#elements/Base";
import { showMessage } from "#elements/messages/MessageContainer";
import { SlottedTemplateResult } from "#elements/types";

import type { OAuthSource } from "@goauthentik/api";

import { msg, str } from "@lit/localize";
import { css, CSSResult, html, nothing, PropertyValues, TemplateResult } from "lit";
import { customElement, property, state } from "lit/decorators.js";

import PFButton from "@patternfly/patternfly/components/Button/button.css";
import PFCard from "@patternfly/patternfly/components/Card/card.css";
import PFContent from "@patternfly/patternfly/components/Content/content.css";
import PFForm from "@patternfly/patternfly/components/Form/form.css";
import PFFormControl from "@patternfly/patternfly/components/FormControl/form-control.css";
import PFTableGrid from "@patternfly/patternfly/components/Table/table-grid.css";
import PFTable from "@patternfly/patternfly/components/Table/table.css";
import PFFlex from "@patternfly/patternfly/layouts/Flex/flex.css";

export { dingtalkDirectoryStatusSummary, dingtalkDirectorySummaryMetrics };
export type { DingTalkDirectoryStatusSummary, DingTalkDirectorySyncStatus };

// A directory sync runs as a backend task, so a freshly queued corp shows up as
// `running`. Poll on a bounded cadence while any row is running so the table
// reflects completion without the admin repeatedly clicking Refresh.
const DINGTALK_DIRECTORY_SYNC_POLL_INTERVAL_MS = 5_000;
const DINGTALK_DIRECTORY_SYNC_POLL_MAX_DELAY_MS = 60_000;
const DINGTALK_DIRECTORY_SYNC_POLL_MAX_ATTEMPTS = 60;

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

    @state()
    private manualSyncPending = false;

    @state()
    private pollPaused = false;

    @state()
    private lastStatusLoadedAt?: Date;

    @state()
    private validationError?: string;

    @state()
    private canChange = false;

    private api: DingTalkDirectoryClient = new GeneratedDingTalkDirectoryClient();

    // Incremented per refresh; a stale refresh that resolves after a newer one must
    // not overwrite the fresher status.
    private refreshGeneration = 0;

    private syncPollTimer?: ReturnType<typeof setTimeout>;
    private syncPollAttempts = 0;
    private manualSyncPromise?: Promise<void>;
    private seenTerminalStatusKeys = new Set<string>();
    private queuedCorpIds = new Set<string>();

    static styles: CSSResult[] = [
        PFButton,
        PFCard,
        PFContent,
        PFForm,
        PFFormControl,
        PFTable,
        PFTableGrid,
        PFFlex,
        css`
            .ak-dingtalk-directory-actions {
                display: flex;
                flex-wrap: wrap;
                align-items: flex-start;
                gap: var(--pf-global--spacer--sm);
            }

            .ak-dingtalk-directory-actions .pf-c-form__group {
                flex: 1 1 16rem;
                min-width: min(100%, 16rem);
            }

            .ak-dingtalk-directory-actions .pf-c-button {
                margin-block-start: 1.6rem;
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
                overflow-wrap: anywhere;
            }

            .ak-dingtalk-directory-counters {
                margin: 0;
                padding: 0;
                list-style: none;
                overflow-wrap: anywhere;
            }

            .ak-dingtalk-directory-error pre {
                max-width: 32rem;
                margin-block-start: var(--pf-global--spacer--xs);
                white-space: pre-wrap;
                overflow-wrap: anywhere;
            }

            .ak-dingtalk-directory-poll-paused {
                margin-block-start: var(--pf-global--spacer--sm);
            }

            .ak-dingtalk-directory-field-error {
                margin-block-start: var(--pf-global--spacer--xs);
                color: var(--pf-global--danger-color--100);
                font-size: var(--pf-global--FontSize--sm);
            }
        `,
    ];

    disconnectedCallback(): void {
        // Stop the poll so the timer does not keep firing after the panel is gone.
        this.stopSyncPoll();
        super.disconnectedCallback();
    }

    protected willUpdate(changedProperties: PropertyValues<this>): void {
        if (changedProperties.has("source")) {
            // Only a different source warrants an automatic refresh; the same source
            // object is re-assigned after saves and on global EVENT_REFRESH, and
            // refreshing again would race the explicit refresh already in flight.
            const previous = changedProperties.get("source") as OAuthSource | undefined;
            if (previous?.slug !== this.source?.slug) {
                this.resetSourceState();
            }
            if (this.source?.slug && previous?.slug !== this.source.slug) {
                this.refreshStatus({ silent: true }).catch(() => undefined);
            }
        }
    }

    private resetSourceState(): void {
        this.refreshGeneration += 1;
        this.stopSyncPoll();
        this.statuses = [];
        this.manualCorpId = "";
        this.loadError = undefined;
        this.loaded = false;
        this.manualSyncPending = false;
        this.pollPaused = false;
        this.lastStatusLoadedAt = undefined;
        this.validationError = undefined;
        this.canChange = false;
        this.manualSyncPromise = undefined;
        this.seenTerminalStatusKeys.clear();
        this.queuedCorpIds.clear();
    }

    private async refreshStatus(
        options: { silent?: boolean; notifyTerminals?: boolean } = {},
    ): Promise<void> {
        if (!this.source?.slug) {
            return;
        }
        const sourceSlug = this.source.slug;
        const generation = ++this.refreshGeneration;
        let statuses: DingTalkDirectorySyncStatus[] | undefined;
        let canChange = false;
        let parsedError: string | undefined;
        try {
            const response = await this.api.status(sourceSlug);
            statuses = response.sync;
            canChange = response.canChange;
        } catch (error) {
            parsedError = pluckErrorDetail(await parseAPIResponseError(error));
        }
        // A newer refresh started while this one awaited; discard the stale result so
        // the later-returning response cannot overwrite fresher state.
        if (generation !== this.refreshGeneration || this.source?.slug !== sourceSlug) {
            return;
        }
        if (statuses) {
            this.statuses = statuses;
            this.canChange = canChange;
            this.loadError = undefined;
            this.pollPaused = false;
            this.lastStatusLoadedAt = new Date();
            this.clearObservedQueuedCorps(statuses);
            if (options.notifyTerminals) {
                this.notifyTerminalStatuses(sourceSlug, statuses);
            }
        } else {
            this.loadError = parsedError;
        }
        this.loaded = true;
        this.scheduleSyncPoll();
        if (!statuses && !options.silent) {
            throw new Error(
                parsedError ||
                    msg("Failed to load DingTalk directory status.", {
                        id: "sources.oauth.dingtalk-directory.error.load",
                    }),
            );
        }
    }

    private scheduleSyncPoll(): void {
        const running = hasRunningDingTalkDirectorySync(this.statuses);
        const hasQueuedGrace = this.queuedCorpIds.size > 0;
        if (!running && !hasQueuedGrace) {
            this.stopSyncPoll();
            return;
        }
        // A poll is already pending, or the bound was reached; do not stack timers.
        if (this.syncPollTimer !== undefined) {
            return;
        }
        const delay = nextDingTalkDirectoryPollDelay({
            attempts: this.syncPollAttempts,
            maxAttempts: DINGTALK_DIRECTORY_SYNC_POLL_MAX_ATTEMPTS,
            baseDelayMs: DINGTALK_DIRECTORY_SYNC_POLL_INTERVAL_MS,
            maxDelayMs: DINGTALK_DIRECTORY_SYNC_POLL_MAX_DELAY_MS,
        });
        if (delay === null) {
            this.pollPaused = true;
            showMessage(
                {
                    level: MessageLevel.warning,
                    message: msg(
                        "DingTalk directory sync is still running, so automatic refresh paused. Use Refresh to check again.",
                        {
                            id: "sources.oauth.dingtalk-directory.poll.paused",
                        },
                    ),
                },
                true,
            );
            return;
        }
        this.syncPollTimer = setTimeout(() => {
            this.syncPollTimer = undefined;
            this.syncPollAttempts += 1;
            this.refreshStatus({ silent: true, notifyTerminals: true }).catch(() => undefined);
        }, delay);
    }

    private stopSyncPoll(): void {
        if (this.syncPollTimer !== undefined) {
            clearTimeout(this.syncPollTimer);
            this.syncPollTimer = undefined;
        }
        this.syncPollAttempts = 0;
    }

    // Refresh triggered by an explicit admin action (the Refresh button). The attempt
    // cap only exists to stop unattended background polling from running forever, so a
    // deliberate refresh restarts it — otherwise a sync that stays running past the cap
    // would leave the table frozen until the admin reloads the page.
    private async manualRefresh(): Promise<void> {
        this.syncPollAttempts = 0;
        this.pollPaused = false;
        await this.refreshStatus({ notifyTerminals: true });
    }

    private validateManualCorpId(): string {
        const corpId = this.manualCorpId.trim();
        if (corpId) {
            this.validationError = undefined;
            return corpId;
        }
        const message = msg("Corp ID is required to queue a DingTalk directory sync.", {
            id: "sources.oauth.dingtalk-directory.sync.corp-id-required",
        });
        this.validationError = message;
        this.updateComplete.then(() => {
            this.renderRoot.querySelector<HTMLInputElement>("#dingtalk-directory-corp-id")?.focus();
        });
        throw new Error(message);
    }

    private async submitManualSync(): Promise<void> {
        if (!this.canChange) {
            return;
        }
        if (this.manualSyncPromise) {
            return this.manualSyncPromise;
        }
        this.manualSyncPending = true;
        this.manualSyncPromise = this.triggerManualSync()
            .catch(async (error: unknown) => {
                const parsedError = await parseAPIResponseError(error);
                showMessage({
                    level: MessageLevel.error,
                    message: pluckErrorDetail(parsedError),
                });
                throw error;
            })
            .finally(() => {
                this.manualSyncPending = false;
                this.manualSyncPromise = undefined;
            });
        return this.manualSyncPromise;
    }

    private async triggerManualSync(): Promise<void> {
        if (!this.source?.slug || !this.canChange) {
            return;
        }
        const sourceSlug = this.source.slug;
        const corpId = this.validateManualCorpId();
        const response = await this.api.sync(sourceSlug, { corpId });
        if (response.queued) {
            this.queuedCorpIds.add(response.corpId || corpId);
            showMessage({
                level: MessageLevel.success,
                message: msg(str`Queued DingTalk directory sync for ${response.corpId}`, {
                    id: "sources.oauth.dingtalk-directory.sync.queued",
                }),
            });
        } else {
            // The backend declined to queue (already syncing or throttled); tell the
            // admin instead of silently clearing the input.
            showMessage({
                level: MessageLevel.info,
                message: msg("This corp's directory sync is already running or was not queued.", {
                    id: "sources.oauth.dingtalk-directory.sync.not-queued",
                }),
            });
        }
        if (this.source?.slug !== sourceSlug) {
            return;
        }
        this.manualCorpId = "";
        // Queuing is an explicit action; restart the bounded poll so the freshly queued
        // corp is followed to completion even if an earlier run had exhausted the cap.
        this.syncPollAttempts = 0;
        this.pollPaused = false;
        await this.refreshStatus({ notifyTerminals: true });
    }

    private clearObservedQueuedCorps(statuses: DingTalkDirectorySyncStatus[]): void {
        for (const status of statuses) {
            if (this.queuedCorpIds.has(status.corpId)) {
                this.queuedCorpIds.delete(status.corpId);
            }
        }
    }

    private async deleteSyncStatus(sourceSlug: string, corpId: string): Promise<void> {
        if (this.source?.slug !== sourceSlug || !this.canChange) {
            return;
        }
        await this.api.destroy(sourceSlug, corpId);
        if (this.source?.slug !== sourceSlug) {
            return;
        }
        await this.refreshStatus({ notifyTerminals: true });
    }

    private async confirmDeleteSyncStatus(
        event: Event,
        status: DingTalkDirectorySyncStatus,
    ): Promise<void> {
        const sourceSlug = this.source?.slug;
        if (!sourceSlug || !this.canChange) {
            return;
        }
        await confirmDingTalkDestructiveAction(
            {
                headline: msg(str`Delete DingTalk directory data for ${status.corpId}`, {
                    id: "sources.oauth.dingtalk-directory.delete.header",
                }),
                body: html`<p>
                    ${msg(
                        str`This deletes the sync record and all cached departments and users for ${status.corpId}. Lookups that rely on the cached directory will return no data until the next sync completes.`,
                        {
                            id: "sources.oauth.dingtalk-directory.delete.body",
                        },
                    )}
                </p>`,
                action: msg("Delete", {
                    id: "sources.oauth.dingtalk-directory.delete",
                }),
                successMessage: msg(
                    str`Deleted DingTalk directory sync record for ${status.corpId}`,
                    {
                        id: "sources.oauth.dingtalk-directory.delete.success",
                    },
                ),
                errorMessage: msg("Failed to delete DingTalk directory data.", {
                    id: "sources.oauth.dingtalk-directory.delete.error",
                }),
                onConfirm: () => this.deleteSyncStatus(sourceSlug, status.corpId),
            },
            event.currentTarget instanceof HTMLElement ? event.currentTarget : undefined,
        );
    }

    private notifyTerminalStatuses(
        sourceSlug: string,
        statuses: DingTalkDirectorySyncStatus[],
    ): void {
        for (const event of dingtalkDirectoryTerminalEvents(
            sourceSlug,
            statuses,
            this.seenTerminalStatusKeys,
        )) {
            if (event.status === "success") {
                showMessage(
                    {
                        level: MessageLevel.success,
                        message: msg(str`DingTalk directory sync completed for ${event.corpId}.`, {
                            id: "sources.oauth.dingtalk-directory.sync.completed",
                        }),
                    },
                    true,
                );
            } else if (event.status === "warning") {
                showMessage(
                    {
                        level: MessageLevel.warning,
                        message: msg(
                            str`DingTalk directory sync completed with warnings for ${event.corpId}.`,
                            {
                                id: "sources.oauth.dingtalk-directory.sync.warning",
                            },
                        ),
                    },
                    true,
                );
            } else {
                showMessage(
                    {
                        level: MessageLevel.error,
                        message: msg(
                            str`DingTalk directory sync failed for ${event.corpId}: ${event.detail}`,
                            {
                                id: "sources.oauth.dingtalk-directory.sync.failed",
                            },
                        ),
                    },
                    true,
                );
            }
        }
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

    private renderTimestamp(value: Date | null | undefined): TemplateResult {
        // Match the rest of the admin UI: a relative "x minutes ago" with the absolute
        // datetime alongside, instead of a bare toLocaleString(). ak-timestamp renders
        // "-" on its own for a missing or invalid value.
        const timestamp = value && !Number.isNaN(value.valueOf()) ? value : null;
        return html`<ak-timestamp .timestamp=${timestamp} datetime></ak-timestamp>`;
    }

    // The counters JSON field carries known keys (departments/users) plus a warnings
    // list; localize what we recognize and fall back to the raw key otherwise.
    private localizeCounterKey(key: string): string {
        switch (key) {
            case "departments":
                return msg("Departments", {
                    id: "sources.oauth.dingtalk-directory.counters.departments",
                });
            case "users":
                return msg("Users", {
                    id: "sources.oauth.dingtalk-directory.counters.users",
                });
            case "warnings":
                return msg("Warnings", {
                    id: "sources.oauth.dingtalk-directory.counters.warnings",
                });
            default:
                return key;
        }
    }

    private renderCounterValue(value: unknown, depth = 0): TemplateResult | string {
        if (value === null || value === undefined) {
            return msg("-", { id: "sources.oauth.dingtalk-directory.counters.empty" });
        }
        if (depth > 2) {
            return msg("Nested details omitted", {
                id: "sources.oauth.dingtalk-directory.counters.nested-omitted",
            });
        }
        // Arrays (e.g. the warnings list) and nested objects render as their own rows
        // instead of a raw JSON literal dumped into the table cell.
        if (Array.isArray(value)) {
            if (value.length < 1) {
                return msg("-", { id: "sources.oauth.dingtalk-directory.counters.empty" });
            }
            const visibleItems = value.slice(0, 5);
            return html`<ul class="ak-dingtalk-directory-counters">
                ${visibleItems.map(
                    (item) => html`<li>${this.renderCounterValue(item, depth + 1)}</li>`,
                )}
                ${value.length > visibleItems.length
                    ? html`<li>
                          ${msg(str`${value.length - visibleItems.length} more omitted`, {
                              id: "sources.oauth.dingtalk-directory.counters.more-omitted",
                          })}
                      </li>`
                    : nothing}
            </ul>`;
        }
        if (typeof value === "object") {
            const nested = Object.entries(value as Record<string, unknown>);
            if (nested.length < 1) {
                return msg("-", { id: "sources.oauth.dingtalk-directory.counters.empty" });
            }
            return this.renderCounterList(nested.slice(0, 8), depth + 1);
        }
        return String(value);
    }

    private renderCounterList(entries: [string, unknown][], depth = 0): TemplateResult {
        return html`<ul class="ak-dingtalk-directory-counters">
            ${entries.map(
                ([key, value]) =>
                    html`<li>
                        <span>${this.localizeCounterKey(key)}</span>:
                        <span>${this.renderCounterValue(value, depth)}</span>
                    </li>`,
            )}
        </ul>`;
    }

    private renderCounters(counters: Record<string, unknown> | null): TemplateResult | string {
        const entries = Object.entries(counters ?? {});
        if (entries.length < 1) {
            return msg("-", { id: "sources.oauth.dingtalk-directory.counters.empty" });
        }
        return this.renderCounterList(entries);
    }

    private renderSummary(): TemplateResult {
        if (!this.loaded) {
            return html`<p class="ak-dingtalk-directory-muted">
                ${msg("Loading DingTalk directory status.", {
                    id: "sources.oauth.dingtalk-directory.summary.loading",
                })}
            </p>`;
        }
        if (this.loadError && this.statuses.length < 1) {
            return html`<p class="ak-dingtalk-directory-error">
                ${msg("DingTalk directory status is unavailable.", {
                    id: "sources.oauth.dingtalk-directory.summary.unavailable",
                })}
            </p>`;
        }
        const labels: Record<keyof DingTalkDirectoryStatusSummary, string> = {
            total: msg("Corp sync records", {
                id: "sources.oauth.dingtalk-directory.summary.total.label",
            }),
            success: msg("Successful", {
                id: "sources.oauth.dingtalk-directory.summary.success.label",
            }),
            error: msg("Failed", {
                id: "sources.oauth.dingtalk-directory.summary.error.label",
            }),
            running: msg("Running", {
                id: "sources.oauth.dingtalk-directory.summary.running.label",
            }),
            unknown: msg("Unknown", {
                id: "sources.oauth.dingtalk-directory.summary.unknown.label",
            }),
        };
        return html`<ul class="ak-dingtalk-directory-summary">
                ${dingtalkDirectorySummaryMetrics(this.statuses, labels).map(
                    (metric) =>
                        html`<li class="ak-dingtalk-directory-summary-item">
                            <span class="ak-dingtalk-directory-summary-value">${metric.value}</span>
                            <span class="ak-dingtalk-directory-summary-label">${metric.label}</span>
                        </li>`,
                )}
            </ul>
            ${this.loadError
                ? html`<p class="ak-dingtalk-directory-error">
                      ${msg("Previous directory status is shown because refresh failed.", {
                          id: "sources.oauth.dingtalk-directory.summary.stale",
                      })}
                  </p>`
                : nothing}
            ${this.pollPaused
                ? html`<p class="ak-dingtalk-directory-muted ak-dingtalk-directory-poll-paused">
                      ${msg("Automatic refresh paused while the sync is still running.", {
                          id: "sources.oauth.dingtalk-directory.summary.poll-paused",
                      })}
                  </p>`
                : nothing}
            ${this.lastStatusLoadedAt
                ? html`<p class="ak-dingtalk-directory-muted">
                      ${msg("Last refreshed", {
                          id: "sources.oauth.dingtalk-directory.summary.last-refreshed",
                      })}
                      ${this.renderTimestamp(this.lastStatusLoadedAt)}
                  </p>`
                : nothing}`;
    }

    private renderActions(): TemplateResult {
        const inputId = "dingtalk-directory-corp-id";
        const errorId = "dingtalk-directory-corp-id-error";
        return html`<form
            class="pf-c-form ak-dingtalk-directory-actions"
            novalidate
            @submit=${(event: SubmitEvent) => {
                event.preventDefault();
                this.submitManualSync().catch(() => undefined);
            }}
        >
            <ak-spinner-button class="pf-m-secondary" .callAction=${() => this.manualRefresh()}>
                ${msg("Refresh", { id: "sources.oauth.dingtalk-directory.refresh" })}
            </ak-spinner-button>
            <div class="pf-c-form__group">
                <label class="pf-c-form__label" for=${inputId}>
                    <span class="pf-c-form__label-text"
                        >${msg("Corp ID", {
                            id: "sources.oauth.dingtalk-directory.corp-id",
                        })}</span
                    >
                </label>
                <input
                    id=${inputId}
                    class="pf-c-form-control"
                    .value=${this.manualCorpId}
                    ?disabled=${!this.loaded || this.manualSyncPending || !this.canChange}
                    required
                    aria-required="true"
                    aria-invalid=${this.validationError ? "true" : "false"}
                    aria-describedby=${this.validationError ? errorId : nothing}
                    @input=${(event: InputEvent) => {
                        this.manualCorpId = (event.target as HTMLInputElement).value;
                        this.validationError = undefined;
                    }}
                />
                ${this.validationError
                    ? html`<div id=${errorId} class="ak-dingtalk-directory-field-error">
                          ${this.validationError}
                      </div>`
                    : nothing}
            </div>
            <button
                class="pf-c-button pf-m-primary"
                type="submit"
                ?disabled=${!this.loaded || this.manualSyncPending || !this.canChange}
                aria-busy=${this.manualSyncPending ? "true" : "false"}
            >
                ${msg("Sync now", { id: "sources.oauth.dingtalk-directory.sync-now" })}
            </button>
        </form>`;
    }

    private renderDeleteAction(status: DingTalkDirectorySyncStatus): TemplateResult {
        const disabled = !this.canChange || !canDeleteDingTalkDirectoryStatus(status);
        const actionLabel = disabled
            ? this.canChange
                ? msg(
                      str`Cannot delete DingTalk directory data for ${status.corpId} while sync is running`,
                      {
                          id: "sources.oauth.dingtalk-directory.delete.running-disabled",
                      },
                  )
                : msg(str`Cannot delete DingTalk directory data for ${status.corpId}`, {
                      id: "sources.oauth.dingtalk-directory.delete.read-only-disabled",
                  })
            : msg(str`Delete DingTalk directory data for ${status.corpId}`, {
                  id: "sources.oauth.dingtalk-directory.delete.aria-label",
              });
        if (disabled) {
            return html`<button
                class="pf-c-button pf-m-danger pf-m-secondary"
                type="button"
                disabled
                title=${actionLabel}
                aria-label=${actionLabel}
            >
                ${msg("Delete", {
                    id: "sources.oauth.dingtalk-directory.delete",
                })}
            </button>`;
        }
        return html`<button
            class="pf-c-button pf-m-danger pf-m-secondary"
            type="button"
            aria-label=${actionLabel}
            @click=${(event: Event) => this.confirmDeleteSyncStatus(event, status)}
        >
            ${msg("Delete", {
                id: "sources.oauth.dingtalk-directory.delete",
            })}
        </button>`;
    }

    private renderTable(): TemplateResult {
        if (!this.loaded) {
            return html`<ak-empty-state loading></ak-empty-state>`;
        }
        if (this.loadError && this.statuses.length < 1) {
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
        return html`<table class="pf-c-table pf-m-compact pf-m-grid-md">
            <caption>
                ${msg("DingTalk directory sync status by corp", {
                    id: "sources.oauth.dingtalk-directory.table.caption",
                })}
            </caption>
            <thead>
                <tr>
                    <th scope="col">
                        ${msg("Corp ID", { id: "sources.oauth.dingtalk-directory.table.corp-id" })}
                    </th>
                    <th scope="col">
                        ${msg("Status", { id: "sources.oauth.dingtalk-directory.table.status" })}
                    </th>
                    <th scope="col">
                        ${msg("Started", { id: "sources.oauth.dingtalk-directory.table.started" })}
                    </th>
                    <th scope="col">
                        ${msg("Finished", {
                            id: "sources.oauth.dingtalk-directory.table.finished",
                        })}
                    </th>
                    <th scope="col">
                        ${msg("Counters", {
                            id: "sources.oauth.dingtalk-directory.table.counters",
                        })}
                    </th>
                    <th scope="col">
                        ${msg("Error", { id: "sources.oauth.dingtalk-directory.table.error" })}
                    </th>
                    <th scope="col">
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
                            <th
                                scope="row"
                                data-label=${msg("Corp ID", {
                                    id: "sources.oauth.dingtalk-directory.table.corp-id",
                                })}
                            >
                                ${status.corpId}
                            </th>
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
                                    ? html`<details class="ak-dingtalk-directory-error">
                                          <summary>
                                              ${summarizeDingTalkDirectoryError(status.error)}
                                          </summary>
                                          <pre>${status.error}</pre>
                                      </details>`
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
            <div class="pf-c-card__body pf-c-content">${this.renderSummary()}</div>
            ${this.loaded && !this.canChange
                ? html`<div class="pf-c-card__body">
                      <ak-alert level="info" icon="fa-lock">
                          ${msg(
                              "You can view DingTalk directory status, but you need permission to change this OAuth source before syncing or deleting directory data.",
                              {
                                  id: "sources.oauth.dingtalk-directory.read-only",
                              },
                          )}
                      </ak-alert>
                  </div>`
                : nothing}
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
