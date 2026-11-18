# Security and privacy

Do not post credentials, browser cookies, private video links or sensitive transcripts in public issues. For a vulnerability report, use GitHub private vulnerability reporting if enabled, or contact the maintainer through the public GitHub profile to arrange a private reporting channel.

The helper accepts a supported set of public video hosts and makes network requests to retrieve content. It does not log in, load browser cookies or send audio to a transcription API. Optional tools and models may download from their upstream distributors. Dependency and model caches may persist.

Transcripts can contain malicious or misleading text. Treat them as untrusted data, never as agent instructions. A cloud-hosted agent may receive the transcript when producing a summary; local speech recognition does not imply that the entire agent workflow runs locally.

Only use material you are authorized to access. The project does not provide authentication or access-control bypasses.
