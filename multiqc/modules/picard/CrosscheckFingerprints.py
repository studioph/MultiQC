""" MultiQC submodule to parse output from Picard CrosscheckFingerprints """

import logging
import re
from collections import OrderedDict
from csv import DictReader
from itertools import chain, groupby

from multiqc import config
from multiqc.plots import table
from multiqc.utils.util_functions import strtobool

# Initialize the logger
log = logging.getLogger(__name__)

# This is a subset, the rest of the fields are self-descriptive
FIELD_DESCRIPTIONS = {
    "LEFT_SAMPLE": "The name of the left sample.",
    "LEFT_GROUP_VALUE": "The name of the left data-type group.",
    "RIGHT_SAMPLE": "The name of the right sample.",
    "RIGHT_GROUP_VALUE": "The name of the right data-type group.",
    "RESULT": "The categorical result of comparing the calculated LOD score against the threshold.",
    "DATA_TYPE": "The datatype used for the comparison.",
    "LOD_SCORE": "Log10 of the probability that the samples come from the same individual.",
    "LOD_SCORE_TUMOR_NORMAL": "LOD score with the assumption that Left is a Tumor.",
    "LOD_SCORE_NORMAL_TUMOR": "LOD score with the assumption that Right is a Tumor.",
    "LOD_THRESHOLD": "The LOD threshold used for this pairwise comparison.",
    "TUMOR_AWARENESS": "Whether this pairwise comparison was flagged for tumor awareness",
}


def parse_reports(module):
    """
    Find Picard CrosscheckFingerprints reports and parse their data.

    Stores the data in "Sample/Group - Sample/Group" groups since CrosscheckFingerprints
    does pairwise comparisons between samples at the level selected by `--CROSSCHECK_BY`.
    """

    data_by_sample = dict()

    # Go through logs and find Metrics
    for f in module.find_log_files("picard/crosscheckfingerprints", filehandles=True):
        # Parse an individual CrosscheckFingerprints Report
        (metrics, comments) = _take_till(f["f"], lambda line: line.startswith("#") or line == "\n")
        header = next(metrics).rstrip("\n").split("\t")
        if "LEFT_GROUP_VALUE" not in header:
            # Not a CrosscheckFingerprints Report
            continue
        reader = DictReader(metrics, fieldnames=header, delimiter="\t")
        # Parse out the tumor awareness option and the lod threshold setting if possible
        tumor_awareness, lod_threshold = _parse_cli(comments[1])
        for i, row in enumerate(reader):
            # Check if this row contains samples that should be ignored
            if module.is_ignore_sample(row["LEFT_SAMPLE"]) or module.is_ignore_sample(row["RIGHT_SAMPLE"]):
                continue

            # Clean the sample names
            row["LEFT_SAMPLE"] = module.clean_s_name(row["LEFT_SAMPLE"], f)
            row["LEFT_GROUP_VALUE"] = module.clean_s_name(row["LEFT_GROUP_VALUE"], f)
            row["RIGHT_SAMPLE"] = module.clean_s_name(row["RIGHT_SAMPLE"], f)
            row["RIGHT_GROUP_VALUE"] = module.clean_s_name(row["RIGHT_GROUP_VALUE"], f)

            # Set the cli options of interest for this file
            row["LOD_THRESHOLD"] = lod_threshold
            row["TUMOR_AWARENESS"] = tumor_awareness
            data_by_sample[i] = row

            module.add_data_source(f, section="CrosscheckFingerprints")

    # Only add sections if we found data
    if len(data_by_sample) == 0:
        return 0

    # Superfluous function call to confirm that it is used in this module
    # Replace None with actual version if it is available
    module.add_software_version(None)

    # Write data to file
    module.write_data_file(data_by_sample, f"{module.anchor}_crosscheckfingerprints")

    # For each sample, flag if any comparisons that don't start with "Expected"
    # A sample that does not have all "Expected" will show as `False` and be Red
    general_stats_data = _create_general_stats_data(data_by_sample)
    general_stats_headers = {
        "Crosschecks All Expected": {
            "title": "Crosschecks",
            "description": "All results for samples CrosscheckFingerprints were as expected.",
        }
    }
    module.general_stats_addcols(general_stats_data, general_stats_headers, namespace="CrosscheckFingerprints")

    # Add a table section to the report
    module.add_section(
        name="Crosscheck Fingerprints",
        anchor=f"{module.anchor}-crosscheckfingerprints",
        description="Pairwise identity checking between samples and groups.",
        helptext="""
        Checks that all data in the set of input files comes from the same individual, based on the selected group granularity.
        """,
        plot=table.plot(
            data_by_sample,
            _get_table_headers(data_by_sample),
            {
                "namespace": module.name,
                "id": f"{module.anchor}_crosscheckfingerprints_table",
                "table_title": f"{module.name}: Crosscheck Fingerprints",
                "save_file": True,
                "col1_header": "ID",
                "no_beeswarm": True,
            },
        ),
    )

    return len(data_by_sample)


def _take_till(iterator, fn):
    """
    Take from an iterator till `fn` returns false.

    Returns the iterator with the value that caused false at the front, and all the lines skipped till then as a list.
    """
    headers = []
    try:
        val = next(iterator)
        while fn(val):
            headers.append(val)
            val = next(iterator)
    except StopIteration:
        return ()

    return chain([val], iterator), headers


def _parse_cli(line):
    """Parse the Picard CLI invocation that is stored in the header section of the file."""
    tumor_awareness_regex = r"CALCULATE_TUMOR_AWARE_RESULTS(\s|=)(\w+)"
    lod_threshold_regex = r"LOD_THRESHOLD(\s|=)(\S+)"

    tumor_awareness = None
    lod_threshold = None

    tumor_awareness_match = re.search(tumor_awareness_regex, line)
    if tumor_awareness_match is not None:
        tumor_awareness = strtobool(tumor_awareness_match.group(2))

    lod_threshold_match = re.search(lod_threshold_regex, line)
    if lod_threshold_match is not None:
        lod_threshold = float(lod_threshold_match.group(2))

    return tumor_awareness, lod_threshold


def _get_table_headers(data_by_sample):
    """Create the headers config"""

    table_cols = [
        "RESULT",
        "DATA_TYPE",
        "LOD_THRESHOLD",
        "LOD_SCORE",
    ]
    table_cols_hidden = [
        "LEFT_RUN_BARCODE",
        "LEFT_LANE",
        "LEFT_MOLECULAR_BARCODE_SEQUENCE",
        "LEFT_LIBRARY",
        "LEFT_FILE",
        "RIGHT_RUN_BARCODE",
        "RIGHT_LANE",
        "RIGHT_MOLECULAR_BARCODE_SEQUENCE",
        "RIGHT_LIBRARY",
        "RIGHT_FILE",
        "DATA_TYPE",
    ]

    # Allow customisation from the MultiQC config
    picard_config = getattr(config, "picard_config", {})
    table_cols = picard_config.get("CrosscheckFingerprints_table_cols", table_cols)
    table_cols_hidden = picard_config.get("CrosscheckFingerprints_table_cols_hidden", table_cols_hidden)

    # Add the Tumor/Normal LOD scores if any pair had the tumor_awareness flag set
    if any(row["TUMOR_AWARENESS"] for row in data_by_sample.values()):
        table_cols += [
            "LOD_SCORE_TUMOR_NORMAL",
            "LOD_SCORE_NORMAL_TUMOR",
        ]
    else:
        table_cols_hidden += [
            "LOD_SCORE_TUMOR_NORMAL",
            "LOD_SCORE_NORMAL_TUMOR",
        ]

    # Add Left and Right Sample names / groups, keeping it as minimal as possible
    def sample_group_are_same(x):
        return x["LEFT_SAMPLE"] == x["LEFT_GROUP_VALUE"] and x["RIGHT_SAMPLE"] == x["RIGHT_GROUP_VALUE"]

    if all(sample_group_are_same(values) for values in data_by_sample.values()):
        table_cols = [
            "LEFT_SAMPLE",
            "RIGHT_SAMPLE",
        ] + table_cols
        table_cols_hidden += ["LEFT_GROUP_VALUE", "RIGHT_GROUP_VALUE"]
    else:
        table_cols = [
            "LEFT_SAMPLE",
            "LEFT_GROUP_VALUE",
            "RIGHT_SAMPLE",
            "RIGHT_GROUP_VALUE",
        ] + table_cols

    headers = OrderedDict()
    for h in FIELD_DESCRIPTIONS:
        # Skip anything not set to visible
        if h not in table_cols:
            continue

        # Set up the configuration for the column
        h_title = h.replace("_", " ").strip().lower().capitalize().replace("Lod", "LOD")
        headers[h] = {
            "title": h_title,
            "description": FIELD_DESCRIPTIONS.get(h),
            "namespace": "CrosscheckFingerprints",
            "scale": False,
        }

        # Rename Result to be a longer string so the table formats more nicely
        if h == "RESULT":
            headers[h]["title"] = "Categorical Result"
            headers[h]["cond_formatting_rules"] = {
                "pass": [{"s_contains": "EXPECTED_"}],
                "warn": [{"s_eq": "INCONCLUSIVE"}],
                "fail": [{"s_contains": "UNEXPECTED_"}],
            }

        # Add appropriate colors for LOD scores
        if h.startswith("LOD"):
            headers[h]["scale"] = "RdYlGn"
            headers[h]["shared_key"] = "LOD"
            headers[h]["bars_zero_centrepoint"] = True

        if h in table_cols_hidden:
            headers[h]["hidden"] = True

    return headers


def _create_general_stats_data(in_data):
    """
    Look at the LEFT_SAMPLE fields and determine if there are any pairs for that samples
    that don't have a RESULT that startswith EXPECTED.
    """
    out_data = dict()
    flattened = (row for row in in_data.values())
    sorted_by_left_sample = sorted(flattened, key=lambda r: r["LEFT_SAMPLE"])

    for group, values in groupby(sorted_by_left_sample, key=lambda r: r["LEFT_SAMPLE"]):
        passfail = "Pass" if all(v["RESULT"].startswith("EXPECTED") for v in values) else "Fail"
        out_data[group] = {"Crosschecks All Expected": passfail}

    return out_data
