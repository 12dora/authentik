import { parseAPIResponseError, pluckErrorDetail } from "#common/errors/network";

import { BaseAPI, Configuration, ResponseError } from "@goauthentik/api";

import { describe, expect, it } from "vitest";

class FailingApi extends BaseAPI {
    public async requestBrokenEndpoint(): Promise<void> {
        await this.request({
            path: "/broken/",
            method: "POST",
            headers: {},
        });
    }
}

function failingApi(body: unknown): FailingApi {
    return new FailingApi(
        new Configuration({
            fetchApi: async () =>
                new Response(JSON.stringify(body), {
                    status: 400,
                    headers: {
                        "content-type": "application/json",
                    },
                }),
        }),
    );
}

describe("API response error handling", () => {
    it("throws the unmodified generated ResponseError with the response attached", async () => {
        const api = failingApi({ detail: ["Broken."] });

        const error = await api.requestBrokenEndpoint().catch((thrown: unknown) => thrown);

        expect(error).toBeInstanceOf(ResponseError);
        expect((error as ResponseError).response.status).toBe(400);
    });

    it("plucks DRF array details from a parsed error response", async () => {
        const api = failingApi({
            detail: ["DingTalk departments can only be loaded for an authorized company."],
        });

        const error = await api.requestBrokenEndpoint().catch((thrown: unknown) => thrown);
        const parsed = await parseAPIResponseError(error);

        expect(pluckErrorDetail(parsed)).toBe(
            "DingTalk departments can only be loaded for an authorized company.",
        );
    });

    it("joins multiple array messages into one readable detail", () => {
        expect(pluckErrorDetail({ detail: ["First problem.", "Second problem."] })).toBe(
            "First problem., Second problem.",
        );
    });

    it("still returns string details unchanged", () => {
        expect(pluckErrorDetail({ detail: "Plain detail." })).toBe("Plain detail.");
    });

    it("falls back to the generic message when no detail field is usable", () => {
        expect(pluckErrorDetail({ detail: [] })).toBe(
            "Internal server error: An unexpected error occurred",
        );
    });
});
