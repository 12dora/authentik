import "#elements/EmptyState";

import { PFSize } from "#common/enums";

import { AKModal } from "#elements/dialogs/ak-modal";
import { SlottedTemplateResult } from "#elements/types";

import {
    buildDingTalkDepartmentTreeRows,
    DingTalkDepartmentNode,
    dingtalkDepartmentPageWindow,
    filterDingTalkDepartmentTreeRows,
    invertLoadedDingTalkDepartmentInput,
    selectLoadedDingTalkDepartmentInput,
    splitDingTalkDepartmentIds,
    toggleDingTalkDepartmentTreeInput,
} from "#admin/sources/oauth/DingTalkAllowlistPanelState";

import { msg, str } from "@lit/localize";
import { css, html } from "lit";
import { customElement, property, state } from "lit/decorators.js";

import PFCheck from "@patternfly/patternfly/components/Check/check.css";
import PFTable from "@patternfly/patternfly/components/Table/table.css";

// Client-side page size for the department tree; large directories would
// otherwise render thousands of rows into the DOM at once.
const DINGTALK_DEPARTMENT_PAGE_SIZE = 50;

/**
 * Modal dialog for picking allowed DingTalk departments from the loaded
 * directory tree. Selection is edited locally (as the department-input string)
 * and only handed back through {@linkcode onApply} when the admin confirms,
 * so cancelling the dialog never touches the allowlist model.
 */
@customElement("ak-source-oauth-dingtalk-department-picker")
export class DingTalkDepartmentPickerModal extends AKModal {
    public static styles = [
        ...AKModal.styles,
        PFCheck,
        PFTable,
        css`
            .ak-dingtalk-picker-toolbar {
                display: flex;
                gap: var(--pf-global--spacer--sm);
                flex-wrap: wrap;
                align-items: center;
                margin-block-end: var(--pf-global--spacer--sm);
            }

            .ak-dingtalk-picker-filter {
                min-width: 14rem;
                flex: 1;
            }

            .ak-dingtalk-picker-pager {
                display: flex;
                gap: var(--pf-global--spacer--sm);
                align-items: center;
                margin-block-start: var(--pf-global--spacer--sm);
            }

            .ak-dingtalk-picker-muted {
                color: var(--pf-global--Color--200);
            }

            .ak-dingtalk-picker-count {
                margin-inline-end: auto;
            }
        `,
    ];

    public override size = PFSize.Large;

    /** Loaded directory tree for the company being edited. */
    @property({ attribute: false })
    public departments: DingTalkDepartmentNode[] = [];

    /** Department-input string being edited (IDs separated by spaces). */
    @property()
    public value = "";

    /** Called with the edited department-input string when the admin applies. */
    @property({ attribute: false })
    public onApply?: (value: string) => void;

    @state()
    private filter = "";

    @state()
    private page = 1;

    // True while the filter input has an active IME composition; suppresses the
    // state rewrite (and thus the `.value` write-back) that would break Chinese input.
    private composing = false;

    private startComposition = (): void => {
        this.composing = true;
    };

    private setFilter(value: string): void {
        this.filter = value;
        // Reset to the first page so the filtered result set starts from the top.
        this.page = 1;
    }

    private handleFilterInput(event: Event): void {
        if (this.composing) {
            return;
        }
        this.setFilter((event.target as HTMLInputElement).value);
    }

    private handleFilterCompositionEnd(event: CompositionEvent): void {
        this.composing = false;
        this.setFilter((event.target as HTMLInputElement).value);
    }

    private toggleDepartment(deptId: string, selected: boolean): void {
        this.value = toggleDingTalkDepartmentTreeInput(
            this.value,
            this.departments,
            deptId,
            selected,
        );
    }

    private selectAll(): void {
        this.value = selectLoadedDingTalkDepartmentInput(this.value, this.departments);
    }

    private invertSelection(): void {
        this.value = invertLoadedDingTalkDepartmentInput(this.value, this.departments);
    }

    private apply = (): void => {
        this.onApply?.(this.value);
        this.close();
    };

    protected override renderActions(): SlottedTemplateResult {
        const selectedCount = splitDingTalkDepartmentIds(this.value).length;
        return html`<footer class="ak-c-dialog__footer" part="actions">
            <span class="ak-dingtalk-picker-muted ak-dingtalk-picker-count">
                ${msg(str`Selected department IDs: ${selectedCount}`, {
                    id: "sources.oauth.dingtalk-allowlist.picker.selected-count",
                })}
            </span>
            <button type="button" class="pf-c-button pf-m-primary" @click=${this.apply}>
                ${msg("Apply selection", {
                    id: "sources.oauth.dingtalk-allowlist.picker.apply",
                })}
            </button>
            <button type="button" class="pf-c-button pf-m-link" @click=${this.closeListener}>
                ${msg("Cancel", { id: "common.actions.cancel" })}
            </button>
        </footer>`;
    }

    private renderPager(pageWindow: ReturnType<typeof dingtalkDepartmentPageWindow>) {
        if (pageWindow.totalPages < 2) {
            return html``;
        }
        return html`<div class="ak-dingtalk-picker-pager">
            <button
                type="button"
                class="pf-c-button pf-m-secondary pf-m-small"
                ?disabled=${pageWindow.page <= 1}
                @click=${() => {
                    this.page = pageWindow.page - 1;
                }}
            >
                ${msg("Previous", { id: "sources.oauth.dingtalk-allowlist.departments.page.prev" })}
            </button>
            <span class="ak-dingtalk-picker-muted">
                ${msg(
                    str`Showing ${pageWindow.start + 1}–${pageWindow.end} of ${pageWindow.total}`,
                    {
                        id: "sources.oauth.dingtalk-allowlist.departments.page.range",
                    },
                )}
            </span>
            <button
                type="button"
                class="pf-c-button pf-m-secondary pf-m-small"
                ?disabled=${pageWindow.page >= pageWindow.totalPages}
                @click=${() => {
                    this.page = pageWindow.page + 1;
                }}
            >
                ${msg("Next", { id: "sources.oauth.dingtalk-allowlist.departments.page.next" })}
            </button>
        </div>`;
    }

    protected override render(): SlottedTemplateResult {
        // Wrap in the dialog-body container so the picker gets the modal's
        // standard padding and scroll behavior instead of overflowing the dialog.
        return html`<div class="ak-c-dialog__body ak-m-thin-scrollbar" role="region">
            ${this.renderPicker()}
        </div>`;
    }

    private renderPicker(): SlottedTemplateResult {
        if (this.departments.length < 1) {
            return html`<ak-empty-state icon="pf-icon-enterprise">
                <span
                    >${msg("No departments were loaded for this company.", {
                        id: "sources.oauth.dingtalk-allowlist.picker.empty",
                    })}</span
                >
            </ak-empty-state>`;
        }
        const selected = new Set(splitDingTalkDepartmentIds(this.value));
        const allRows = buildDingTalkDepartmentTreeRows(this.departments, selected);
        const filteredRows = filterDingTalkDepartmentTreeRows(allRows, this.filter);
        const pageWindow = dingtalkDepartmentPageWindow(
            filteredRows.length,
            this.page,
            DINGTALK_DEPARTMENT_PAGE_SIZE,
        );
        const pageRows = filteredRows.slice(pageWindow.start, pageWindow.end);

        return html`<div class="ak-dingtalk-picker-toolbar">
                <input
                    class="pf-c-form-control ak-dingtalk-picker-filter"
                    type="search"
                    .value=${this.filter}
                    aria-label=${msg("Filter departments by ID or name", {
                        id: "sources.oauth.dingtalk-allowlist.departments.filter.aria-label",
                    })}
                    placeholder=${msg("Filter departments", {
                        id: "sources.oauth.dingtalk-allowlist.departments.filter.placeholder",
                    })}
                    @compositionstart=${this.startComposition}
                    @compositionend=${this.handleFilterCompositionEnd}
                    @input=${this.handleFilterInput}
                />
                <button
                    type="button"
                    class="pf-c-button pf-m-secondary pf-m-small"
                    @click=${() => this.selectAll()}
                >
                    ${msg("Select all", {
                        id: "sources.oauth.dingtalk-allowlist.departments.select-all",
                    })}
                </button>
                <button
                    type="button"
                    class="pf-c-button pf-m-secondary pf-m-small"
                    @click=${() => this.invertSelection()}
                >
                    ${msg("Invert selection", {
                        id: "sources.oauth.dingtalk-allowlist.departments.invert",
                    })}
                </button>
            </div>
            <table class="pf-c-table pf-m-compact pf-m-grid-md" role="grid">
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
                            ${msg("Name", {
                                id: "sources.oauth.dingtalk-allowlist.department.name",
                            })}
                        </th>
                        <th>
                            ${msg("Parent ID", {
                                id: "sources.oauth.dingtalk-allowlist.department.parent-id",
                            })}
                        </th>
                    </tr>
                </thead>
                <tbody>
                    ${pageRows.length < 1
                        ? html`<tr>
                              <td colspan="4" class="ak-dingtalk-picker-muted">
                                  ${msg("No departments match the filter.", {
                                      id: "sources.oauth.dingtalk-allowlist.departments.filter.empty",
                                  })}
                              </td>
                          </tr>`
                        : pageRows.map(
                              (row) =>
                                  html`<tr>
                                      <td
                                          data-label=${msg("Allowed", {
                                              id: "sources.oauth.dingtalk-allowlist.department.allowed",
                                          })}
                                      >
                                          <input
                                              class="pf-c-check__input"
                                              type="checkbox"
                                              aria-label=${msg(
                                                  str`Allow ${row.department.name} (${row.department.deptId})`,
                                                  {
                                                      id: "sources.oauth.dingtalk-allowlist.department.checkbox.aria-label",
                                                  },
                                              )}
                                              .checked=${row.selection === "checked"}
                                              .indeterminate=${row.selection === "indeterminate"}
                                              @change=${(event: InputEvent) => {
                                                  this.toggleDepartment(
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
            </table>
            ${this.renderPager(pageWindow)}`;
    }
}

declare global {
    interface HTMLElementTagNameMap {
        "ak-source-oauth-dingtalk-department-picker": DingTalkDepartmentPickerModal;
    }
}
