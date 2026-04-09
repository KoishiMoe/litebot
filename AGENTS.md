Some important facts:
1. handler.finish() will always throw a FinishedException **by design**. Avoid using it in a try-except block that catches all exceptions, as it may lead to unintended consequences.
2. This project uses uv for dependency management. Avoid using pip directly or editing pyproject.toml manually to add dependencies. 
