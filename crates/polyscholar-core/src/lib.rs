// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (c) 2026 Kelvin-LH and contributors.
//! Small validation reference; no PDF processing or network operations.
use std::collections::BTreeMap;

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct BoundingBox {
    pub left: f64,
    pub top: f64,
    pub right: f64,
    pub bottom: f64,
}

impl BoundingBox {
    pub fn is_valid(self) -> bool {
        let values = [self.left, self.top, self.right, self.bottom];
        values.iter().all(|x| x.is_finite() && (0.0..=1.0).contains(x))
            && self.left < self.right
            && self.top < self.bottom
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EvidenceAnchor {
    pub block_id: String,
    pub source_revision: String,
    pub page: u32,
}

pub fn validate_evidence(anchors: &[EvidenceAnchor], available: &[EvidenceAnchor]) -> bool {
    !anchors.is_empty() && anchors.iter().all(|a| a.page > 0 && available.contains(a))
}

/// Compare opaque protected tokens as a multiset, not just a set.
/// Callers must extract tokens from source and output; semantic accuracy is separate.
pub fn protected_tokens_match(source: &[String], translated: &[String]) -> bool {
    fn counts(tokens: &[String]) -> BTreeMap<&str, usize> {
        let mut result = BTreeMap::new();
        for token in tokens {
            *result.entry(token.as_str()).or_insert(0) += 1;
        }
        result
    }
    counts(source) == counts(translated)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TracePoint {
    DocumentParsed,
    ModelRequested,
    SummaryCreated,
    CitationExported,
    ArtifactExported,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Outcome {
    Succeeded,
    Failed,
    Cancelled,
}

/// No document text, credentials, device identifiers or remote sink.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LocalAuditEvent {
    pub sequence: u64,
    pub point: TracePoint,
    pub outcome: Outcome,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_nonfinite_and_reversed_coordinates() {
        let good = BoundingBox { left: 0.0, top: 0.0, right: 1.0, bottom: 1.0 };
        assert!(good.is_valid());
        assert!(!BoundingBox { left: f64::NAN, ..good }.is_valid());
        assert!(!BoundingBox { left: 1.0, ..good }.is_valid());
        assert!(!BoundingBox { right: 1.1, ..good }.is_valid());
    }

    #[test]
    fn rejects_wrong_page_and_revision_even_for_same_block() {
        let a = EvidenceAnchor { block_id: "b1".into(), source_revision: "r1".into(), page: 1 };
        assert!(validate_evidence(&[a.clone()], &[a.clone()]));
        let wrong = EvidenceAnchor { page: 2, ..a.clone() };
        assert!(!validate_evidence(&[wrong], &[a.clone()]));
        let stale = EvidenceAnchor { source_revision: "r2".into(), ..a.clone() };
        assert!(!validate_evidence(&[stale], &[a]));
        assert!(!validate_evidence(&[], &[]));
    }

    #[test]
    fn detects_missing_or_duplicated_protected_tokens() {
        let a = vec!["F1".into(), "F1".into(), "C2".into()];
        assert!(protected_tokens_match(&a, &["C2".into(), "F1".into(), "F1".into()]));
        assert!(!protected_tokens_match(&a, &["F1".into(), "C2".into()]));
        assert!(!protected_tokens_match(&a, &["F1".into(), "F1".into(), "C3".into()]));
    }
}
