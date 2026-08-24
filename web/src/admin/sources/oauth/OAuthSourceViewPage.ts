import "#admin/policies/BoundPoliciesList";
import "#admin/rbac/ak-rbac-object-permission-page";
import "#admin/sources/oauth/OAuthSourceDiagram";
import "#admin/events/ObjectChangelog";
import "#elements/CodeMirror";
import "#elements/EmptyState";
import "#elements/LoadingOverlay";
import "#elements/Tabs";
import "#elements/buttons/SpinnerButton/index";

import { aki } from "#common/api/client";
import { EVENT_REFRESH } from "#common/constants";

import { AKElement } from "#elements/Base";
import { modalInvoker } from "#elements/dialogs";
import { sourceBindingTypeNotices } from "#elements/sources/utils";
import { SlottedTemplateResult } from "#elements/types";

import renderDescriptionList from "#components/DescriptionList";

import { OAuthSourceForm } from "#admin/sources/oauth/OAuthSourceForm";

import { ModelEnum, OAuthSource, ProviderTypeEnum, SourcesApi } from "@goauthentik/api";

import { msg } from "@lit/localize";
import { CSSResult, html, nothing } from "lit";
import { customElement, property, state } from "lit/decorators.js";

import PFButton from "@patternfly/patternfly/components/Button/button.css";
import PFCard from "@patternfly/patternfly/components/Card/card.css";
import PFContent from "@patternfly/patternfly/components/Content/content.css";
import PFDescriptionList from "@patternfly/patternfly/components/DescriptionList/description-list.css";
import PFPage from "@patternfly/patternfly/components/Page/page.css";
import PFGrid from "@patternfly/patternfly/layouts/Grid/grid.css";

export function ProviderToLabel(provider?: ProviderTypeEnum): string {
    switch (provider) {
        case undefined:
            return "";
        case ProviderTypeEnum.Apple:
            return "Apple";
        case ProviderTypeEnum.Dingtalk:
            return "DingTalk";
        case ProviderTypeEnum.Discord:
            return "Discord";
        case ProviderTypeEnum.Facebook:
            return "Facebook";
        case ProviderTypeEnum.Entraid:
            return "Entra ID";
        case ProviderTypeEnum.Github:
            return "GitHub";
        case ProviderTypeEnum.Gitlab:
            return "GitLab";
        case ProviderTypeEnum.Google:
            return "Google";
        case ProviderTypeEnum.Mailcow:
            return "Mailcow";
        case ProviderTypeEnum.Openidconnect:
            return msg("Generic OpenID Connect");
        case ProviderTypeEnum.Okta:
            return "Okta";
        case ProviderTypeEnum.Patreon:
            return "Patreon";
        case ProviderTypeEnum.Reddit:
            return "Reddit";
        case ProviderTypeEnum.Slack:
            return "Slack";
        case ProviderTypeEnum.Twitter:
            return "Twitter";
        case ProviderTypeEnum.Twitch:
            return "Twitch";
        case ProviderTypeEnum.Wechat:
            return "WeChat";
        case ProviderTypeEnum.UnknownDefaultOpenApi:
            return msg("Unknown provider type");
    }
}

@customElement("ak-source-oauth-view")
export class OAuthSourceViewPage extends AKElement {
    @property({ type: String })
    set sourceSlug(value: string) {
        this.loadSource(value);
    }

    @property({ attribute: false })
    source?: OAuthSource;

    @state()
    private loadError = false;

    @state()
    private sourceLoading = false;

    @state()
    private dingTalkPanelsLoaded = false;

    @state()
    private activeDingTalkPanel: "page-dingtalk-allowlist" | "page-dingtalk-directory" | null =
        null;

    @state()
    private activatedDingTalkPanels: ReadonlySet<
        "page-dingtalk-allowlist" | "page-dingtalk-directory"
    > = new Set();

    private requestedSlug = "";
    private requestGeneration = 0;
    private dingTalkPanelsLoading?: Promise<void>;

    private async loadSource(slug: string): Promise<void> {
        const generation = ++this.requestGeneration;
        const sameLoadedSource = this.source?.slug === slug;
        this.requestedSlug = slug;
        if (!sameLoadedSource) {
            this.source = undefined;
            this.activeDingTalkPanel = null;
            this.activatedDingTalkPanels = new Set();
        }
        this.loadError = false;
        this.sourceLoading = true;
        try {
            const source = await aki(SourcesApi).sourcesOauthRetrieve({ slug });
            if (generation === this.requestGeneration && this.requestedSlug === slug) {
                this.source = source;
                if (source.providerType === ProviderTypeEnum.Dingtalk) {
                    this.loadDingTalkPanels();
                }
            }
        } catch {
            if (generation === this.requestGeneration && this.requestedSlug === slug) {
                this.loadError = true;
            }
        } finally {
            if (generation === this.requestGeneration && this.requestedSlug === slug) {
                this.sourceLoading = false;
            }
        }
    }

    private loadDingTalkPanels(): Promise<void> {
        this.dingTalkPanelsLoading ??= Promise.all([
            import("#admin/sources/oauth/DingTalkAllowlistPanel"),
            import("#admin/sources/oauth/DingTalkDirectoryPanel"),
        ]).then(() => {
            this.dingTalkPanelsLoaded = true;
        });

        return this.dingTalkPanelsLoading;
    }

    private activateDingTalkPanel(
        panel: "page-dingtalk-allowlist" | "page-dingtalk-directory",
    ): void {
        this.activeDingTalkPanel = panel;
        this.activatedDingTalkPanels = new Set([...this.activatedDingTalkPanels, panel]);
        this.loadDingTalkPanels();
    }

    static styles: CSSResult[] = [PFPage, PFButton, PFGrid, PFContent, PFCard, PFDescriptionList];

    constructor() {
        super();
        this.addEventListener(EVENT_REFRESH, () => {
            if (!this.source?.pk) return;
            this.sourceSlug = this.source?.slug;
        });
    }

    render(): SlottedTemplateResult {
        if (this.loadError && !this.source) {
            return html`<ak-empty-state icon="fa-exclamation-triangle">
                <span
                    >${msg("Failed to load OAuth source.", {
                        id: "sources.oauth.view.error.load",
                    })}</span
                >
                <button
                    slot="primary"
                    type="button"
                    class="pf-c-button pf-m-primary"
                    @click=${() => this.loadSource(this.requestedSlug)}
                >
                    ${msg("Retry", { id: "common.actions.retry" })}
                </button>
            </ak-empty-state>`;
        }
        if (!this.source) {
            return html`<ak-empty-state loading full-height></ak-empty-state>`;
        }
        return html`<main>
            <ak-tabs>
                <div
                    role="tabpanel"
                    tabindex="0"
                    slot="page-overview"
                    id="page-overview"
                    aria-label="${msg("Overview")}"
                    class="pf-c-page__main-section pf-m-no-padding-mobile"
                >
                    <div class="pf-l-grid pf-m-gutter">
                        <div class="pf-c-card pf-l-grid__item pf-m-4-col">
                            <div class="pf-c-card__title">${msg("Details")}</div>
                            <div class="pf-c-card__body">
                                ${renderDescriptionList([
                                    [msg("Name"), html`${this.source.name}`],
                                    [
                                        msg("Provider Type"),
                                        html`${ProviderToLabel(this.source.providerType)}`,
                                    ],
                                    [msg("Callback URL"), html`${this.source.callbackUrl}`],
                                    [msg("Access Key"), html`${this.source.consumerKey}`],
                                    [
                                        msg("Authorization URL"),
                                        html`${this.source.type?.authorizationUrl ||
                                        this.source.authorizationUrl}`,
                                    ],
                                    [
                                        msg("Token URL"),
                                        html`${this.source.type?.accessTokenUrl ||
                                        this.source.accessTokenUrl}`,
                                    ],
                                    [
                                        msg("Related actions"),
                                        html`<button
                                            class="pf-c-button pf-m-primary pf-m-block"
                                            ${modalInvoker(OAuthSourceForm, {
                                                instancePk: this.source.slug,
                                            })}
                                        >
                                            ${msg("Edit")}
                                        </button>`,
                                    ],
                                ])}
                            </div>
                        </div>
                        <div class="pf-c-card pf-l-grid__item pf-m-8-col">
                            <div class="pf-c-card__title">${msg("Diagram")}</div>
                            <div class="pf-c-card__body">
                                <ak-source-oauth-diagram
                                    .source=${this.source}
                                ></ak-source-oauth-diagram>
                            </div>
                        </div>
                    </div>
                </div>
                <div
                    role="tabpanel"
                    tabindex="0"
                    slot="page-changelog"
                    id="page-changelog"
                    aria-label="${msg("Changelog")}"
                    class="pf-c-page__main-section pf-m-no-padding-mobile"
                >
                    <div class="pf-l-grid pf-m-gutter">
                        <div class="pf-c-card pf-l-grid__item pf-m-12-col">
                            <ak-object-changelog
                                targetModelPk=${this.source.pk || ""}
                                targetModelName=${ModelEnum.AuthentikSourcesOauthOauthsource}
                            >
                            </ak-object-changelog>
                        </div>
                    </div>
                </div>
                ${this.source.providerType === ProviderTypeEnum.Dingtalk
                    ? html`<div
                              role="tabpanel"
                              tabindex="0"
                              slot="page-dingtalk-allowlist"
                              id="page-dingtalk-allowlist"
                              aria-label="${msg("DingTalk Allowlist", {
                                  id: "sources.oauth.dingtalk-allowlist.tab",
                              })}"
                              class="pf-c-page__main-section pf-m-no-padding-mobile"
                              @activate=${() =>
                                  this.activateDingTalkPanel("page-dingtalk-allowlist")}
                          >
                              <div class="pf-l-grid pf-m-gutter">
                                  ${this.dingTalkPanelsLoaded &&
                                  this.activatedDingTalkPanels.has("page-dingtalk-allowlist")
                                      ? html`<ak-source-oauth-dingtalk-allowlist
                                            class="pf-l-grid__item pf-m-12-col"
                                            .source=${this.source}
                                        ></ak-source-oauth-dingtalk-allowlist>`
                                      : nothing}
                              </div>
                          </div>
                          <div
                              role="tabpanel"
                              tabindex="0"
                              slot="page-dingtalk-directory"
                              id="page-dingtalk-directory"
                              aria-label="${msg("DingTalk Directory", {
                                  id: "sources.oauth.dingtalk-directory.tab",
                              })}"
                              class="pf-c-page__main-section pf-m-no-padding-mobile"
                              @activate=${() =>
                                  this.activateDingTalkPanel("page-dingtalk-directory")}
                          >
                              <div class="pf-l-grid pf-m-gutter">
                                  ${this.dingTalkPanelsLoaded &&
                                  this.activatedDingTalkPanels.has("page-dingtalk-directory")
                                      ? html`<ak-source-oauth-dingtalk-directory
                                            class="pf-l-grid__item pf-m-12-col"
                                            .source=${this.activeDingTalkPanel ===
                                            "page-dingtalk-directory"
                                                ? this.source
                                                : undefined}
                                        ></ak-source-oauth-dingtalk-directory>`
                                      : nothing}
                              </div>
                          </div>`
                    : nothing}
                <div
                    role="tabpanel"
                    tabindex="0"
                    slot="page-policy-binding"
                    id="page-policy-binding"
                    aria-label="${msg("Policy Bindings")}"
                    class="pf-c-page__main-section pf-m-no-padding-mobile"
                >
                    <div class="pf-l-grid pf-m-gutter">
                        <div class="pf-c-card pf-l-grid__item pf-m-12-col">
                            <div class="pf-c-card__title">
                                ${msg(
                                    `These bindings control which users can access this source.
            You can only use policies here as access is checked before the user is authenticated.`,
                                )}
                            </div>
                            <ak-bound-policies-list
                                .target=${this.source.pk}
                                .typeNotices=${sourceBindingTypeNotices()}
                                .policyEngineMode=${this.source.policyEngineMode}
                            >
                            </ak-bound-policies-list>
                        </div>
                    </div>
                </div>
                <ak-rbac-object-permission-page
                    role="tabpanel"
                    tabindex="0"
                    slot="page-permissions"
                    id="page-permissions"
                    aria-label="${msg("Permissions")}"
                    model=${ModelEnum.AuthentikSourcesOauthOauthsource}
                    objectPk=${this.source.pk}
                ></ak-rbac-object-permission-page>
            </ak-tabs>
            ${this.sourceLoading ? html`<ak-loading-overlay></ak-loading-overlay>` : nothing}
        </main>`;
    }
}

declare global {
    interface HTMLElementTagNameMap {
        "ak-source-oauth-view": OAuthSourceViewPage;
    }
}
