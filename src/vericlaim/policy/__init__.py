"""Policy-document retrieval: parsing, chunking, embedding, storage, search.

The package owns source 1 (policy wordings) and provides the storage and retrieval
layer that source 4 (scanned documents, ``vericlaim.scanned``) also uses. See
:mod:`vericlaim.policy.models` for why the two share one collection.
"""
