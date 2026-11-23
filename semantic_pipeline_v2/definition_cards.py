"""Definition cards: the text and metadata of each field and metric of one Ossie model.

Two text variants feed the vector leg:
  definition_only  the name and the description (what the old index embedded);
  context          label, description, synonyms, example questions, role, unit, values, concept and dataset.
Missing metadata stays missing. A card never invents a unit or a grain.
"""

from semantic_pipeline_v2.model_cache import load_yaml
import json
from pathlib import Path

import yaml

from .core import normalize

ROOT = Path(__file__).resolve().parents[1]
MAX_DESCRIPTION = 600


def _helios(node):
    for ext in node.get("custom_extensions") or []:
        if ext.get("vendor_name") == "HELIOS":
            raw = ext.get("data")
            return json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    return {}


def _values(values):
    """code = meaning pairs. The source's own 'not recorded' wording is dropped, the code stays."""
    if not isinstance(values, dict):
        return ""
    parts = [str(code) if "not recorded" in str(meaning) else f"{code} = {meaning}"
             for code, meaning in list(values.items())[:8]]
    return "; ".join(parts)[:400]


def load_cards(workspace):
    """{definition id: card} for every field and metric the workspace exposes."""
    structured = workspace["knowledge"]["structured"]
    model_source = next(s for s in workspace["sources"] if s["id"] == structured["modelSource"])
    raw = (ROOT / model_source["citation"]["path"]).read_bytes()
    records = normalize(raw, model_source["citation"]["sha256"])["records"]
    model = load_yaml(raw)
    top = {}
    for ext in model.get("custom_extensions") or []:  # the one model-level extension holds concepts and areas
        top = json.loads(ext["data"]) if isinstance(ext.get("data"), str) else dict(ext.get("data") or {})
        break
    concepts = {c["id"]: c for c in top.get("concepts", []) if isinstance(c, dict) and "id" in c}
    areas = {a["id"]: a for a in top.get("subject_areas", []) if isinstance(a, dict) and "id" in a}
    datasets = {}
    for dataset in model.get("datasets", []):
        data = _helios(dataset)
        area = areas.get(data.get("subject_area"), {})
        datasets[dataset["name"]] = {"label": data.get("label") or dataset.get("label") or dataset["name"],
                                     "subject_area": area.get("label")}
    prefix = workspace["id"] + ":definition:"
    cards = {}
    for record in records:
        _, kind, local = record["source_id"].split("/", 2)
        if kind not in ("field", "metric"):
            continue
        meta, sem = record["source_metadata"], record["semantics"]
        name = local.rsplit("/", 1)[-1]
        tables = [local.split("/", 1)[0]] if kind == "field" else list(record.get("input_tables") or [])[:1]
        dataset = datasets.get(tables[0], {}) if tables else {}
        concept = concepts.get(sem["concept"]) if sem.get("concept") else None
        label = sem.get("label") or meta.get("label") or name
        description = record["definition"][:MAX_DESCRIPTION]
        lines = [f"{label} ({name}). {description}"]
        if sem["synonyms"]:
            lines.append("Also called: " + ", ".join(sem["synonyms"]) + ".")
        if sem["examples"]:
            lines.append("Example questions: " + " ".join(sem["examples"][:3]))
        facts = [f"Role: {sem['role']}" if sem.get("role") else "",
                 f"Unit: {sem['unit']}" if sem.get("unit") else ""]
        if sem.get("values"):
            facts.append("Values: " + _values(sem["values"]))
        if any(facts):
            lines.append(". ".join(f for f in facts if f) + ".")
        if concept:
            lines.append(f"Concept: {concept.get('label')}. {concept.get('definition', '')}".strip())
        if dataset:
            area = f" ({dataset['subject_area']})" if dataset.get("subject_area") else ""
            lines.append(f"Dataset: {dataset['label']}{area}" + (f" [{tables[0]}]" if tables else "") + ".")
        cards[prefix + record["source_id"]] = {
            "id": prefix + record["source_id"], "kind": kind, "name": name, "label": label,
            "description": description, "synonyms": list(sem["synonyms"]), "role": sem.get("role"),
            "unit": sem.get("unit"), "datatype": meta.get("datatype"), "concept": sem.get("concept"),
            "dataset": tables[0] if tables else None,
            "definition_only": f"{name}: {description}", "context": " ".join(lines),
        }
    return cards
