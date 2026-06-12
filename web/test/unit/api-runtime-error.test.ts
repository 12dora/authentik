import { BaseAPI, Configuration } from "@goauthentik/api";

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

describe("generated API runtime errors", () => {
    it("includes JSON validation details in ResponseError messages", async () => {
        const api = new FailingApi(
            new Configuration({
                fetchApi: async () =>
                    new Response(
                        JSON.stringify({
                            target: ["Invalid pk does not exist."],
                        }),
                        {
                            status: 400,
                            headers: {
                                "content-type": "application/json",
                            },
                        },
                    ),
            }),
        );

        await expect(api.requestBrokenEndpoint()).rejects.toThrow(
            "target: Invalid pk does not exist.",
        );
    });
});
