# Prompt taxonomy

Valid options that can be passed with prompts to the AI (e.g. for site analysis, extraction, or filtering).

## File format: YAML

- **Format:** YAML (human-editable, comments, matches existing configs in this repo).
- **Path:** `data/taxonomy/prompt_taxonomy.yaml` (or per-domain files under `data/taxonomy/`).

## Schema

Top-level key is `dimensions`. Each dimension has a list of allowed values.

```yaml
dimensions:
  category:
    values: [location, capacity, product, services, solution, person, other]
  type:
    values: [hq, data center, office, power, GPUs, other]
```

- **Dimension name** (e.g. `category`, `type`): the parameter name you pass to the prompt or API.
- **values**: list of valid string options (lowercase, snake_case or single tokens recommended for consistency).

Optional: add a `description` for the dimension (for docs or UI):

```yaml
dimensions:
  category:
    description: "High-level classification of the extracted or requested information"
    values: [location, capacity, product, services, solution, person, other]
```

Optional extended form with per-value descriptions (for few-shot prompts or tooling):

```yaml
dimensions:
  category:
    values:
      - id: location
        description: Physical place, geography, or site
      - id: capacity
        description: Scale metrics (MW, GPUs, etc.)
      - id: product
      # ...
```

Use the **simple form** (list of strings) unless you need descriptions. Code can validate that a supplied `category` or `type` is in the corresponding `values` list.
