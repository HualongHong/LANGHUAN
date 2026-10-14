#!/usr/bin/env Rscript
# Convert WOS / CNKI plaintext export to a standardized CSV using bibliometrix.
# Usage:
#   Rscript scripts/bibliometrix_convert.R <input_file> <output_csv> [dbsource]
#
# Supported dbsource values: wos, scopus, dimensions, pubmed, cochrane, cnki
# If omitted, bibliometrix will try to auto-detect based on file content.

suppressPackageStartupMessages({
  if (!require("bibliometrix", quietly = TRUE)) {
    stop("Package 'bibliometrix' is not installed. Install with: install.packages('bibliometrix')")
  }
})

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2) {
  stop("Usage: Rscript bibliometrix_convert.R <input_file> <output_csv> [dbsource]")
}

input_file <- args[1]
output_csv <- args[2]
dbsource <- if (length(args) >= 3) args[3] else NULL

if (!file.exists(input_file)) {
  stop(paste0("Input file not found: ", input_file))
}

# bibliometrix::convertToDf can read a vector of file paths.
files <- c(input_file)

tryCatch(
  {
    # Auto-detect datasource if not provided.
    if (is.null(dbsource) || dbsource == "auto") {
      # Simple heuristic: CNKI Refworks exports start with "RT Journal Article".
      header <- paste(readLines(input_file, n = 5, warn = FALSE), collapse = " ")
      if (grepl("RT ", header, fixed = TRUE) && grepl("中文|CNKI|cnki", header)) {
        dbsource <- "cnki"
      } else {
        dbsource <- "isi"
      }
    }

    if (dbsource %in% c("wos", "isi")) {
      M <- bibliometrix::convertToDf(files, dbsource = "isi")
    } else if (dbsource == "cnki") {
      M <- bibliometrix::convertToDf(files, dbsource = "cnki")
    } else if (dbsource == "scopus") {
      M <- bibliometrix::convertToDf(files, dbsource = "scopus")
    } else if (dbsource == "pubmed") {
      M <- bibliometrix::convertToDf(files, dbsource = "pubmed")
    } else if (dbsource == "cochrane") {
      M <- bibliometrix::convertToDf(files, dbsource = "cochrane")
    } else if (dbsource == "dimensions") {
      M <- bibliometrix::convertToDf(files, dbsource = "dimensions")
    } else {
      stop(paste0("Unsupported datasource: ", dbsource))
    }

    # Optionally enrich metadata. Ignore errors if metaTagExtraction is unavailable.
    M <- tryCatch(
      bibliometrix::metaTagExtraction(M, Field = "AU_CO"),
      error = function(e) M
    )

    # Ensure standard uppercase column names for downstream compatibility.
    names(M) <- toupper(names(M))

    write.csv(M, file = output_csv, row.names = FALSE, fileEncoding = "UTF-8")
  },
  error = function(e) {
    # Write a minimal error file so Python can detect failure.
    msg <- data.frame(error = conditionMessage(e))
    write.csv(msg, file = output_csv, row.names = FALSE, fileEncoding = "UTF-8")
    quit(status = 1)
  }
)
