"""File-type knowledge: languages, binaries, lock files, generated files and secrets."""

from __future__ import annotations

from pathlib import PurePosixPath

EXTENSION_LANGUAGES: dict[str, str] = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".mts": "typescript", ".cts": "typescript", ".tsx": "typescript",
    ".md": "markdown", ".mdx": "markdown", ".markdown": "markdown", ".rst": "rst", ".txt": "text",
    ".json": "json", ".jsonc": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".ini": "ini", ".cfg": "ini", ".conf": "ini", ".properties": "ini",
    ".html": "html", ".htm": "html", ".css": "css", ".scss": "scss", ".sass": "sass", ".less": "less",
    ".vue": "vue", ".svelte": "svelte", ".astro": "astro",
    ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin", ".kts": "kotlin", ".scala": "scala",
    ".swift": "swift", ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp",
    ".cs": "csharp", ".fs": "fsharp", ".rb": "ruby", ".php": "php", ".pl": "perl", ".lua": "lua",
    ".dart": "dart", ".ex": "elixir", ".exs": "elixir", ".erl": "erlang", ".hs": "haskell",
    ".clj": "clojure", ".ml": "ocaml", ".r": "r", ".jl": "julia", ".m": "objective-c",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell", ".fish": "shell", ".ps1": "powershell",
    ".psm1": "powershell", ".bat": "batch", ".cmd": "batch",
    ".sql": "sql", ".graphql": "graphql", ".gql": "graphql", ".proto": "protobuf", ".prisma": "prisma",
    ".xml": "xml", ".gradle": "groovy", ".groovy": "groovy", ".tf": "terraform", ".hcl": "terraform",
    ".ipynb": "jupyter", ".csproj": "xml", ".sln": "text", ".dockerfile": "dockerfile",
}

FILENAME_LANGUAGES: dict[str, str] = {
    "dockerfile": "dockerfile", "makefile": "makefile", "gnumakefile": "makefile",
    "cmakelists.txt": "cmake", "procfile": "text", "gemfile": "ruby", "rakefile": "ruby",
    "jenkinsfile": "groovy", "vagrantfile": "ruby", "license": "text", "licence": "text",
    "readme": "text", "changelog": "text", "authors": "text", "notice": "text",
    ".gitignore": "text", ".dockerignore": "text", ".editorconfig": "ini", ".npmrc": "ini",
    ".nvmrc": "text", ".python-version": "text", ".prettierrc": "json", ".eslintrc": "json",
    ".babelrc": "json", "pipfile": "toml", ".env.example": "dotenv", ".env.sample": "dotenv",
    ".env.template": "dotenv",
}

# Languages parsed with tree-sitter into function / class level chunks.
TREE_SITTER_LANGUAGES = {"python", "javascript", "typescript"}

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".icns", ".webp", ".avif", ".tif", ".tiff",
    ".psd", ".ai", ".sketch", ".fig", ".svg", ".eps", ".heic",
    ".mp3", ".mp4", ".wav", ".ogg", ".flac", ".aac", ".m4a", ".avi", ".mov", ".mkv", ".webm", ".wmv",
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war", ".ear", ".zst",
    ".exe", ".dll", ".so", ".dylib", ".a", ".lib", ".o", ".obj", ".pyc", ".pyo", ".pyd", ".class",
    ".wasm", ".bin", ".dat", ".iso", ".img", ".dmg", ".msi", ".apk", ".ipa", ".deb", ".rpm",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp",
    ".db", ".sqlite", ".sqlite3", ".mdb", ".pkl", ".pickle", ".npy", ".npz", ".h5", ".hdf5",
    ".parquet", ".feather", ".arrow", ".onnx", ".pt", ".pth", ".ckpt", ".safetensors", ".tflite",
    ".pb", ".joblib", ".gguf", ".lockb",
}

# Data / log dumps that are text but never useful as code context.
DATA_EXTENSIONS = {".log", ".csv", ".tsv", ".jsonl", ".ndjson"}

LOCK_FILES = {
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lock", "bun.lockb",
    "poetry.lock", "pipfile.lock", "pdm.lock", "uv.lock", "cargo.lock", "composer.lock", "gemfile.lock",
    "go.sum", "mix.lock", "flake.lock", "packages.lock.json", "podfile.lock", "pubspec.lock",
    "gradle.lockfile", "conda-lock.yml",
}

GENERATED_SUFFIXES = (".min.js", ".min.css", ".min.mjs", ".map", ".bundle.js", ".chunk.js", ".pb.go", "_pb2.py")

IGNORED_DIR_NAMES = {
    ".git", ".hg", ".svn", ".bzr", "node_modules", "bower_components", "jspm_packages", "__pycache__",
    ".venv", "venv", "virtualenv", ".tox", ".nox", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".hypothesis", ".cache", ".parcel-cache", ".next", ".nuxt", ".svelte-kit", ".turbo", ".output",
    ".vercel", ".netlify", ".angular", ".expo", ".docusaurus", "dist", "build", "out", "target",
    "coverage", "htmlcov", ".nyc_output", ".gradle", ".idea", ".vs", ".vscode", "site-packages",
    ".eggs", ".terraform", ".serverless", "storybook-static", "Pods", "DerivedData", "vendor",
    ".ipynb_checkpoints", ".pnpm-store", ".yarn",
}

SECRET_NAMES = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", ".netrc", ".pgpass", ".htpasswd"}
SECRET_EXTENSIONS = {".pem", ".key", ".p12", ".pfx", ".keystore", ".jks", ".crt", ".cer", ".der"}
SAFE_ENV_SUFFIXES = (".example", ".sample", ".template", ".dist")


def detect_language(rel_path: str) -> str:
    """Display language for a file (``typescript`` covers both .ts and .tsx)."""
    name = PurePosixPath(rel_path).name.lower()
    if name in FILENAME_LANGUAGES:
        return FILENAME_LANGUAGES[name]
    if name.startswith("dockerfile"):
        return "dockerfile"
    if name.startswith("requirements") and name.endswith(".txt"):
        return "pip-requirements"
    suffix = PurePosixPath(name).suffix
    return EXTENSION_LANGUAGES.get(suffix, "text")


def grammar_for(rel_path: str) -> str | None:
    """Tree-sitter grammar to use for a file, or None for line-window chunking."""
    suffix = PurePosixPath(rel_path).suffix.lower()
    if suffix in (".py", ".pyi"):
        return "python"
    if suffix in (".js", ".mjs", ".cjs", ".jsx"):
        return "javascript"
    if suffix in (".ts", ".mts", ".cts"):
        return "typescript"
    if suffix == ".tsx":
        return "tsx"
    return None


def is_lock_file(name: str) -> bool:
    lower = name.lower()
    return lower in LOCK_FILES or lower.endswith(".lock")


def is_binary_name(name: str) -> bool:
    return PurePosixPath(name.lower()).suffix in BINARY_EXTENSIONS


def is_data_dump(name: str) -> bool:
    return PurePosixPath(name.lower()).suffix in DATA_EXTENSIONS


def is_generated_name(name: str) -> bool:
    lower = name.lower()
    return lower.endswith(GENERATED_SUFFIXES)


def is_secret_file(name: str) -> bool:
    lower = name.lower()
    if lower in SECRET_NAMES or PurePosixPath(lower).suffix in SECRET_EXTENSIONS:
        return True
    if lower == ".env" or (lower.startswith(".env.") and not lower.endswith(SAFE_ENV_SUFFIXES)):
        return True
    return False
