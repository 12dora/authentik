import "#elements/buttons/SpinnerButton/index";

import { parseAPIResponseError, pluckErrorDetail } from "#common/errors/network";
import { MessageLevel } from "#common/messages";

import { renderDialog } from "#elements/dialogs";
import { AKModal } from "#elements/dialogs/ak-modal";
import { showMessage } from "#elements/messages/MessageContainer";
import { SlottedTemplateResult } from "#elements/types";

import { msg, str } from "@lit/localize";
import { html, PropertyValues, TemplateResult } from "lit";
import { customElement, property } from "lit/decorators.js";
import { createRef, ref } from "lit/directives/ref.js";

export interface DingTalkDestructiveActionOptions {
    headline: string;
    body: TemplateResult;
    action: string;
    successMessage: string;
    errorMessage: string;
    onConfirm: () => Promise<unknown>;
}

@customElement("ak-source-oauth-dingtalk-destructive-action")
export class DingTalkDestructiveActionModal extends AKModal {
    @property()
    public override headline: string | null = null;

    @property()
    public action = "";

    @property()
    public successMessage = "";

    @property()
    public errorMessage = "";

    @property({ attribute: false })
    public body: TemplateResult = html``;

    @property({ attribute: false })
    public onConfirm: () => Promise<unknown> = async () => undefined;

    private cancelButtonRef = createRef<HTMLButtonElement>();

    public override formatARIALabel = (): string => this.headline || this.action;

    public override updated(changedProperties: PropertyValues<this>): void {
        super.updated(changedProperties);
        if (this.open) {
            requestAnimationFrame(() => this.cancelButtonRef.value?.focus());
        }
    }

    private async confirm(): Promise<void> {
        try {
            await this.onConfirm();
            showMessage({
                message: this.successMessage,
                level: MessageLevel.success,
            });
            this.close("submitted");
        } catch (error) {
            const parsedError = await parseAPIResponseError(error);
            showMessage({
                message: msg(str`${this.errorMessage}: ${pluckErrorDetail(parsedError)}`),
                level: MessageLevel.error,
            });
            throw error;
        }
    }

    protected override render(): TemplateResult {
        return html`<section class="pf-c-content">${this.body}</section>`;
    }

    protected override renderActions(): SlottedTemplateResult {
        return html`<footer
            aria-label=${msg("Form actions")}
            class="ak-c-dialog__footer"
            part="actions"
        >
            <button
                class="pf-c-button pf-m-link"
                type="button"
                ${ref(this.cancelButtonRef)}
                @click=${this.closeListener}
            >
                ${msg("Cancel", { id: "common.actions.cancel" })}
            </button>
            <ak-spinner-button class="pf-m-danger" .callAction=${() => this.confirm()}>
                ${this.action}
            </ak-spinner-button>
        </footer>`;
    }
}

export async function confirmDingTalkDestructiveAction(
    options: DingTalkDestructiveActionOptions,
    invokerElement?: HTMLElement | null,
): Promise<void> {
    const modal = new DingTalkDestructiveActionModal();
    modal.headline = options.headline;
    modal.body = options.body;
    modal.action = options.action;
    modal.successMessage = options.successMessage;
    modal.errorMessage = options.errorMessage;
    modal.onConfirm = options.onConfirm;

    await renderDialog(modal, {
        invokerElement,
        onDispose: () => invokerElement?.focus(),
    });
}

declare global {
    interface HTMLElementTagNameMap {
        "ak-source-oauth-dingtalk-destructive-action": DingTalkDestructiveActionModal;
    }
}
