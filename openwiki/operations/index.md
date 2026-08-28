# Files

- [Cloud Service Boundaries and Integration Points](cloud-boundaries.md) - Integration points where the classifier connects to Microsoft Graph, Azure Queue Storage, PostgreSQL, and inference providers (Anthropic or Foundry), including seam abstractions, message contracts, and error handling.
- [Cloud Boundaries — Queue and Graph](cloud-seams.md) - Message queue abstraction, Microsoft Graph client, and their authentication modes
- [Configuration Management](configuration.md) - Environment variables, settings singleton, and tuning parameters
- [Container Build, CI, and OIDC Deployment](deployment.md) - Docker image, GitHub Actions workflows, CI gates, OIDC federated credentials, and ACA job setup
- [Error Handling](error-handling.md) - Exception hierarchy, error types, and recovery strategies
- [Local Testing and Live-Fire Stack](local-testing.md) - Set up and run the docker-compose stack to test the end-to-end two-job pipeline locally against real documents, PostgreSQL, and Azurite without cloud infrastructure
- [PostgreSQL State Store](state-store.md) - Database schema, ORM models, and lifecycle of documents and sync state in the cloud pipeline
