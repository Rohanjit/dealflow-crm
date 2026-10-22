# Checking a Dealflow image before you run it

Every released image is built by GitHub Actions from a tagged commit, scanned, and published with:

- a **signature** made with Sigstore's keyless signing (tied to this repository's release workflow, not to a key someone could steal),
- a **build-provenance attestation** (which commit and workflow built it),
- a **software bill of materials** (CycloneDX: every package inside), attached to the image and to the GitHub release.

## Verify the signature

```sh
cosign verify ghcr.io/rohanjit/dealflow-crm@sha256:<digest> \
  --certificate-identity-regexp '^https://github.com/Rohanjit/dealflow-crm/.github/workflows/release.yml@refs/tags/v' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

## Verify where it was built

```sh
gh attestation verify oci://ghcr.io/rohanjit/dealflow-crm@sha256:<digest> --repo Rohanjit/dealflow-crm
```

## Read the bill of materials

```sh
cosign verify-attestation --type cyclonedx ghcr.io/rohanjit/dealflow-crm@sha256:<digest> \
  --certificate-identity-regexp '^https://github.com/Rohanjit/dealflow-crm/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com | jq -r .payload | base64 -d | jq .predicate
```

Run images by digest (`image: ghcr.io/rohanjit/dealflow-crm@sha256:…` in `docker-compose.yml`), not by tag,
so what you checked is what runs.
