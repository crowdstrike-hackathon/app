"""Create a clean-output model alias; preserve the original GPU model."""
import argparse
import asyncio

import httpx


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:11435")
    parser.add_argument("--source", default="redteam:latest")
    parser.add_argument("--target", default="redteam-clean:latest")
    args = parser.parse_args()
    async with httpx.AsyncClient(timeout=180) as client:
        response = await client.post(args.base_url + "/api/show", json={"model": args.source})
        response.raise_for_status()
        original = response.json()["template"]
        if "enable_thinking" not in original or "<|im_start|>" not in original:
            raise RuntimeError("Expected a Qwen Jinja chat template; refusing to modify an unknown format")
        # Use explicit ChatML role boundaries and a closed thinking prefix for tickets.
        fixed = '{{ range .Messages }}<|im_start|>{{ .Role }}\n{{ .Content }}<|im_end|>\n{{ end }}<|im_start|>assistant\n<think>\n\n</think>\n\n'
        response = await client.post(args.base_url + "/api/create", json={
            "model": args.target, "from": args.source, "template": fixed,
            "parameters": {"num_ctx": 16384}, "stream": False,
        })
        if response.is_error:
            raise RuntimeError(response.text)
        response = await client.post(args.base_url + "/api/show", json={"model": args.target})
        response.raise_for_status()
        effective = response.json()["modelfile"]
        if fixed not in effective:
            raise RuntimeError("Server did not retain the corrected Modelfile template")
        print(f"Created {args.target}; original {args.source} preserved.")
        print("Corrected Modelfile template verified.")


if __name__ == "__main__":
    asyncio.run(main())
