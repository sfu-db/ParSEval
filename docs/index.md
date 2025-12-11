# Welcome to MkDocs

For full documentation visit [mkdocs.org](https://www.mkdocs.org).

## Commands

* `mkdocs new [dir-name]` - Create a new project.
* `mkdocs serve` - Start the live-reloading docs server.
* `mkdocs build` - Build the documentation site.
* `mkdocs -h` - Print help message and exit.

## Project layout

    mkdocs.yml    # The configuration file.
    docs/
        index.md  # The documentation homepage.
        ...       # Other markdown pages, images and other files.



# Build dev environment from poetry
## Add package to poetry dependency
```bash
poetry add --group dev func_timeout==4.3.5
```

## Add a runtime dependency
```bash
poetry add <package_name>
poetry add sqlglot
```

## Install only dev dependencies
If you want to include dev dependencies when installing:
```bash
poetry install --with dev
```
If you want to install only the main (non-dev) dependencies:
```bash
poetry install --without dev
```