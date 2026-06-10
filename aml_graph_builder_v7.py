"""
aml_graph_builder_v7.py
========================
ProductionAMLGraphBuilder — v7  (All Leakage Fixes Applied)

What changed from v6
--------------------

LEAKAGE FIXES
  [FIX-L1] _build_money_flow_edges() now accepts train_df and builds
       sender→receiver flow edges using TRAINING DATA ONLY.
       Previously the full dataset was used, leaking future transfer volumes
       into training-time edge weights.

  [FIX-L2] _build_account_account_edges() now accepts train_idx and restricts
       device-sharing co-occurrence detection to training rows only.
       Previously future device sharing / account co-use was visible during
       training, making the connected_to graph forward-looking.

  [FIX-L3] evaluate() AUROC now catches ValueError (single-class validation
       sets) in addition to ImportError.  Previously a val set containing
       only one class (all-fraud or all-legit) would crash training.

NARRATIVE PIPELINE
  [FIX-L4] Added USE_NARRATIVE_CONCAT class flag.  When True, the builder
       automatically concatenates categorical text columns
       (transaction_type, merchant_category, location, device_used)
       into a synthetic "memo" column and feeds it through NarrativeEncoder.
       Only meaningful when those columns contain genuine natural-language
       values; for purely structured categoricals keep False (default).

ADDITIONAL IMPROVEMENTS
  [IMP-1] create_edges_interval_based() now passes train_idx / train_df
       through to the two edge-building methods that need it.

  [IMP-2] main() wires train_idx correctly into create_edges_interval_based().

  [IMP-3] Training loop uncommented and ready to run; includes basic
       early-stopping skeleton (best-val-F1 checkpoint save).

  [IMP-4] evaluate() returns AUPRC (Average Precision) alongside AUROC —
       a better metric for highly imbalanced fraud datasets.

  [IMP-5] compute_class_weights() helper added for cleaner main().

Full account node feature set (13, unchanged from v6):
  historical_txn_count, historical_avg_amount, historical_amount_std,
  user_txn_frequency_24h, user_txn_count_total, bvn_linked,
  persona_fraud_risk_computed, total_sent_amount, avg_sent_amount,
  total_received_amount, avg_received_amount, net_flow, receiver_txn_count

Transaction feature set (unchanged from v6):
  14 tabular + payment_channel OHE + transaction_type OHE + raw_narrative_emb

Edge types (unchanged from v6):
  All v5 edges + (account, money_flow, account)
"""

from __future__ import annotations

import itertools
import warnings
from typing import Optional

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from scipy.special import expit
from sklearn.preprocessing import OneHotEncoder, RobustScaler
from torch_geometric.data import HeteroData
from torch_geometric.loader import NeighborLoader
from torch_geometric.nn import HeteroConv, SAGEConv
from torch_geometric.transforms import ToUndirected

warnings.filterwarnings("ignore")


# ---------------------------------------------------------------------------
# Narrative encoder
# ---------------------------------------------------------------------------

class NarrativeEncoder:
    """
    Encode free-text memo / comment fields into dense embeddings.

    Priority
    --------
    1. sentence-transformers ``all-MiniLM-L6-v2``  (384-d, ~80 MB, CPU-OK)
    2. TF-IDF + TruncatedSVD fallback              (scikit-learn, 64-d)

    Output: float32 ndarray (n, emb_dim).
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2", n_components: int = 64):
        self.model_name   = model_name
        self.n_components = n_components
        self.emb_dim: Optional[int] = None
        self._mode     = "unset"
        self._st_model = None
        self._pipeline = None

    def _try_load_sentence_transformers(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
            self._st_model = SentenceTransformer(self.model_name)
            self._mode = "sentence_transformers"
            print(f"   NarrativeEncoder: sentence-transformers '{self.model_name}'")
        except ImportError:
            self._mode = "bow"
            print("   NarrativeEncoder: falling back to TF-IDF + TruncatedSVD")

    def fit_transform(self, texts: list[str]) -> np.ndarray:
        if self._mode == "unset":
            self._try_load_sentence_transformers()
        if self._mode == "sentence_transformers":
            embs = self._st_model.encode(
                texts, batch_size=256,
                show_progress_bar=False, convert_to_numpy=True,
            ).astype(np.float32)
        else:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.decomposition import TruncatedSVD
            from sklearn.pipeline import Pipeline
            self._pipeline = Pipeline([
                ("tfidf", TfidfVectorizer(max_features=10_000, sublinear_tf=True)),
                ("svd",   TruncatedSVD(n_components=self.n_components, random_state=42)),
            ])
            embs = self._pipeline.fit_transform(texts).astype(np.float32)
        self.emb_dim = embs.shape[1]
        return embs

    def transform(self, texts: list[str]) -> np.ndarray:
        if self._mode == "sentence_transformers":
            return self._st_model.encode(
                texts, batch_size=256,
                show_progress_bar=False, convert_to_numpy=True,
            ).astype(np.float32)
        elif self._pipeline is not None:
            return self._pipeline.transform(texts).astype(np.float32)
        raise RuntimeError("Call fit_transform before transform.")


# ---------------------------------------------------------------------------
# Narrative projection head  (lives ONLY in AMLHeteroGNN)
# ---------------------------------------------------------------------------

class NarrativeProjection(nn.Module):
    """
    Linear(emb_dim, out_dim) → LayerNorm → ReLU

    Trainable inside AMLHeteroGNN so the projection learns fraud-relevant
    text representations end-to-end via the fraud classification loss.
    """

    def __init__(self, emb_dim: int, out_dim: int):
        super().__init__()
        self.proj = nn.Sequential(
            nn.Linear(emb_dim, out_dim),
            nn.LayerNorm(out_dim),
            nn.ReLU(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.proj(x)


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

class ProductionAMLGraphBuilder:
    """
    Builds a PyG HeteroData graph for AML / fraud detection.
    Aligned with the Nigerian Financial Transactions dataset schema.

    Node types  : transaction, account, device, ip, merchant
    Edge types  : see create_edges_interval_based()

    Typical call order
    ------------------
    builder = ProductionAMLGraphBuilder(df, memo_col=None)
    builder.compute_risk_features_no_leakage()
    builder.create_node_mappings_vectorized()
    train_idx, val_idx, test_idx = builder.time_based_train_val_test_split()
    builder.extract_node_features_vectorized(train_idx)
    builder.create_edges_interval_based(train_idx)          # <-- pass train_idx
    hetero_data = builder.build_hetero_data()
    """

    # [D7] Optional feature flags
    USE_USER_TOP_CATEGORY = False
    USE_IP_GEO_REGION     = False

    # [FIX-L4] Set True to auto-build a synthetic narrative from categorical cols.
    # Useful only when those cols contain genuine natural-language content.
    # For pure structured categoricals (Transfer, ATM, Lagos…) keep False —
    # the GNN tabular branch already handles them more efficiently.
    USE_NARRATIVE_CONCAT  = False

    # Columns to concatenate when USE_NARRATIVE_CONCAT=True.
    # Exclude is_fraud, fraud_type, and any numeric column.
    NARRATIVE_CONCAT_COLS = [
        "transaction_type",
        "merchant_category",
        "location",
        "device_used",       # present in some variants of the dataset
        "payment_channel",
        "sender_persona",
    ]

    def __init__(
        self,
        df: pd.DataFrame,
        memo_col: Optional[str] = None,
        narrative_out_dim: int  = 32,
    ):
        self.df               = df.sort_values("timestamp").reset_index(drop=True)
        self.memo_col         = memo_col
        self.narrative_out_dim = narrative_out_dim

        self.scalers: dict[str, RobustScaler] = {
            k: RobustScaler()
            for k in ("account", "transaction", "device", "ip", "merchant")
        }
        self.channel_encoder      = OneHotEncoder(sparse_output=False, handle_unknown="ignore")
        self.txn_type_encoder     = OneHotEncoder(sparse_output=False, handle_unknown="ignore")
        self.top_category_encoder = OneHotEncoder(sparse_output=False, handle_unknown="ignore")
        self.ip_geo_encoder       = OneHotEncoder(sparse_output=False, handle_unknown="ignore")

        self.narrative_encoder: Optional[NarrativeEncoder] = None
        self.narrative_embeddings: Optional[np.ndarray]    = None

        self.tabular_dim:       int = 0
        self.raw_narrative_dim: int = 0

    # ------------------------------------------------------------------
    # 1. Risk features
    # ------------------------------------------------------------------

    def compute_risk_features_no_leakage(
        self,
        force_recompute: bool = False,
    ) -> pd.DataFrame:
        """
        Populate internal *_computed risk-rate columns.

        force_recompute=True  → always run time-decay estimator (safe path).
        force_recompute=False → use dataset columns if present (fast path,
                                only safe if columns were computed historically).
        """
        df = self.df

        _PROVENANCE_WARNING = (
            "   ⚠  Using dataset column '{}' directly.  If this column was\n"
            "      computed over the full dataset (not historical only), it\n"
            "      encodes future fraud labels → target leakage.\n"
            "      Pass force_recompute=True to recompute safely."
        )

        if not force_recompute and "merchant_fraud_rate" in df.columns:
            df["merchant_fraud_rate_computed"] = df["merchant_fraud_rate"]
            print(_PROVENANCE_WARNING.format("merchant_fraud_rate"))
        else:
            print("   Risk features: computing merchant_fraud_rate_computed (time-decay)")
            self._compute_time_decay_rate(df, "merchant_category", "merchant_fraud_rate_computed")

        if not force_recompute and "location_fraud_risk" in df.columns:
            df["location_fraud_rate_computed"] = df["location_fraud_risk"]
            print(_PROVENANCE_WARNING.format("location_fraud_risk"))
        else:
            print("   Risk features: computing location_fraud_rate_computed (time-decay)")
            self._compute_time_decay_rate(df, "location", "location_fraud_rate_computed")

        if not force_recompute and "persona_fraud_risk" in df.columns:
            df["persona_fraud_risk_computed"] = df["persona_fraud_risk"]
            print(_PROVENANCE_WARNING.format("persona_fraud_risk"))
        else:
            if "persona_fraud_risk" not in df.columns:
                print("   Risk features: persona_fraud_risk not found — defaulting to 0.1")
                df["persona_fraud_risk_computed"] = 0.1
            else:
                print("   Risk features: computing persona_fraud_risk_computed (time-decay)")
                self._compute_time_decay_rate(df, "sender_persona", "persona_fraud_risk_computed")

        self.df = df
        return df

    @staticmethod
    def _compute_time_decay_rate(
        df: pd.DataFrame,
        group_col: str,
        out_col: str,
        alpha: float = 1.0,
        beta:  float = 10.0,
        lam:   float = 0.1,
    ) -> None:
        """Smoothed, time-decayed fraud rate (in-place). λ=0.1 (~10-day half-life)."""
        rates = np.full(len(df), np.nan, dtype=np.float64)
        for _, grp in df.groupby(group_col, sort=False):
            idx   = grp.index.to_numpy()
            ts    = grp["timestamp"].values.astype("datetime64[s]").astype(np.float64)
            fraud = grp["is_fraud"].values.astype(np.float64)
            wf = np.zeros(len(idx))
            wt = np.zeros(len(idx))
            for i in range(1, len(idx)):
                dt    = (ts[i] - ts[:i]) / 86400.0
                w     = np.exp(-lam * dt)
                wf[i] = (fraud[:i] * w).sum()
                wt[i] = w.sum()
            rates[idx] = (wf + alpha) / (wt + beta)

        global_rate = df["is_fraud"].mean()
        df[out_col] = np.where(np.isnan(rates), global_rate, rates)

    # ------------------------------------------------------------------
    # 2. Node mappings
    # ------------------------------------------------------------------

    def create_node_mappings_vectorized(self) -> None:
        df = self.df

        all_accounts = pd.concat([df["sender_account"], df["receiver_account"]]).unique()
        _, self.account_mapping = pd.factorize(all_accounts)
        self.account_to_idx  = {a: i for i, a in enumerate(self.account_mapping)}
        self.sender_to_idx   = self.account_to_idx
        self.receiver_to_idx = self.account_to_idx

        self.sender_account_idx   = (
            df["sender_account"].map(self.account_to_idx).fillna(-1).astype(int).values
        )
        self.receiver_account_idx = (
            df["receiver_account"].map(self.account_to_idx).fillna(-1).astype(int).values
        )

        valid = (self.sender_account_idx >= 0) & (self.receiver_account_idx >= 0)
        if not valid.all():
            print(f"   Warning: filtered {(~valid).sum():,} rows with unmapped accounts")
            self.df                   = df[valid].reset_index(drop=True)
            self.sender_account_idx   = self.sender_account_idx[valid]
            self.receiver_account_idx = self.receiver_account_idx[valid]

        df = self.df
        self.txn_id_to_node_idx = {
            tid: i for i, tid in enumerate(df["transaction_id"])
        }
        self.transaction_ids = np.arange(len(df), dtype=np.int64)

        self.device_ids, self.device_mapping = pd.factorize(df["device_hash"])
        self.device_to_idx  = {d: i for i, d in enumerate(self.device_mapping)}

        self.ip_ids, self.ip_mapping = pd.factorize(df["ip_address"])
        self.ip_to_idx = {ip: i for i, ip in enumerate(self.ip_mapping)}

        self.merchant_ids, self.merchant_mapping = pd.factorize(df["merchant_category"])
        self.merchant_to_idx = {m: i for i, m in enumerate(self.merchant_mapping)}

        self.num_transactions = len(self.transaction_ids)
        self.num_accounts     = len(self.account_mapping)
        self.num_devices      = len(self.device_mapping)
        self.num_ips          = len(self.ip_mapping)
        self.num_merchants    = len(self.merchant_mapping)

        print(
            f"   Nodes — Txn: {self.num_transactions:,}  Acc: {self.num_accounts:,}  "
            f"Dev: {self.num_devices:,}  IP: {self.num_ips:,}  Merch: {self.num_merchants:,}"
        )

    # ------------------------------------------------------------------
    # 3. Node features
    # ------------------------------------------------------------------

    def extract_node_features_vectorized(self, train_idx: np.ndarray) -> None:
        """
        Extract and scale all node features.
        Scalers + encoders are fit ONLY on train_idx rows.
        """
        df       = self.df
        train_df = df.iloc[train_idx]

        # [FIX-L4] Auto-build narrative column from categorical cols if requested
        if self.USE_NARRATIVE_CONCAT and self.memo_col is None:
            present_cols = [c for c in self.NARRATIVE_CONCAT_COLS if c in df.columns]
            if present_cols:
                df["_memo_concat"] = df[present_cols].fillna("").apply(
                    lambda row: " ".join(
                        f"{col}={val}" for col, val in row.items() if val != ""
                    ),
                    axis=1,
                )
                self.df      = df
                self.memo_col = "_memo_concat"
                print(
                    f"   [FIX-L4] Synthetic memo built from: {present_cols}\n"
                    f"            Sample: '{df['_memo_concat'].iloc[0]}'"
                )
            else:
                print("   [FIX-L4] USE_NARRATIVE_CONCAT=True but no matching cols found — skipping.")

        # ----------------------------------------------------------------
        # Account nodes (13 features, sender_is_fraud removed — leakage-free)
        # ----------------------------------------------------------------
        tr_sender = train_df.groupby("sender_account").agg(
            historical_txn_count        = ("transaction_id",              "count"),
            historical_avg_amount       = ("amount_ngn",                  "mean"),
            historical_amount_std       = ("amount_ngn",                  "std"),
            user_txn_frequency_24h      = ("user_txn_frequency_24h",      "first"),
            user_txn_count_total        = ("user_txn_count_total",        "first"),
            bvn_linked                  = ("bvn_linked",                  "first"),
            persona_fraud_risk_computed = ("persona_fraud_risk_computed", "first"),
            total_sent_amount           = ("amount_ngn",                  "sum"),
            avg_sent_amount             = ("amount_ngn",                  "mean"),
        ).fillna(0)

        tr_receiver = train_df.groupby("receiver_account").agg(
            total_received_amount = ("amount_ngn",     "sum"),
            avg_received_amount   = ("amount_ngn",     "mean"),
            receiver_txn_count    = ("transaction_id", "count"),
        ).fillna(0)

        acc_df = tr_sender.join(tr_receiver, how="outer").fillna(0)
        acc_df["historical_amount_std"] = acc_df["historical_amount_std"].fillna(0)
        acc_df["net_flow"] = acc_df["total_sent_amount"] - acc_df["total_received_amount"]
        acc_df = acc_df.reindex(self.account_mapping, fill_value=0)

        if self.USE_USER_TOP_CATEGORY and "user_top_category" in df.columns:
            self.top_category_encoder.fit(train_df[["user_top_category"]].fillna("Unknown"))
            top_cat_all = self.top_category_encoder.transform(
                df.groupby("sender_account")["user_top_category"]
                  .first().reindex(self.account_mapping, fill_value="Unknown")
                  .values.reshape(-1, 1)
            ).astype(np.float32)
            train_acc_mask = acc_df.index.isin(train_df["sender_account"].unique())
            base = acc_df.values.astype(np.float32)
            self.scalers["account"].fit(base[train_acc_mask])
            self.account_features = np.hstack([
                self.scalers["account"].transform(base),
                top_cat_all,
            ]).astype(np.float32)
        else:
            train_acc_mask = acc_df.index.isin(train_df["sender_account"].unique())
            self.scalers["account"].fit(acc_df.values[train_acc_mask])
            self.account_features = self.scalers["account"].transform(
                acc_df.values
            ).astype(np.float32)

        # ----------------------------------------------------------------
        # Transaction nodes
        # 14 tabular + payment_channel OHE + transaction_type OHE + narrative
        # ----------------------------------------------------------------
        txn_cols = [
            "amount_ngn",
            "txn_hour",
            "is_weekend",
            "is_salary_week",
            "is_night_txn",
            "txn_count_last_1h",
            "txn_count_last_24h",
            "total_amount_last_1h",
            "avg_gap_between_txns",
            "time_since_last",
            "geospatial_velocity_anomaly",
            "velocity_score",
            "spending_deviation_score",
            "geo_anomaly_score",
        ]
        txn_cols = [c for c in txn_cols if c in df.columns]

        tabular = df[txn_cols].fillna(0).values.astype(np.float32)

        self.channel_encoder.fit(train_df[["payment_channel"]])
        ch_dummies = self.channel_encoder.transform(
            df[["payment_channel"]]
        ).astype(np.float32)

        if "transaction_type" in df.columns:
            self.txn_type_encoder.fit(train_df[["transaction_type"]].fillna("Unknown"))
            txn_type_dummies = self.txn_type_encoder.transform(
                df[["transaction_type"]].fillna("Unknown")
            ).astype(np.float32)
        else:
            txn_type_dummies = np.empty((len(df), 0), dtype=np.float32)

        tab_all = np.hstack([tabular, ch_dummies, txn_type_dummies])
        self.scalers["transaction"].fit(tab_all[train_idx])
        tab_all = self.scalers["transaction"].transform(tab_all).astype(np.float32)

        self.tabular_dim = tab_all.shape[1]

        narr = self._build_narrative_features(df, train_idx)
        self.transaction_features = np.hstack([tab_all, narr])

        # ----------------------------------------------------------------
        # Device nodes
        # ----------------------------------------------------------------
        new_device_col = (
            "new_device_transaction"
            if "new_device_transaction" in df.columns
            else "new_device_for_sender"
        )
        dev_df = df.groupby("device_hash").agg(
            device_seen_count          = ("device_seen_count",    "first"),
            is_device_shared           = ("is_device_shared",     "first"),
            new_device_transaction     = (new_device_col,         "mean"),
            txn_count                  = ("transaction_id",       "count"),
            unique_accounts_per_device = ("sender_account",       "nunique"),
        ).fillna(0)
        dev_df["shared_device_score"] = (
            dev_df["unique_accounts_per_device"] / np.log1p(dev_df["txn_count"])
        )
        dev_df = dev_df.reindex(self.device_mapping, fill_value=0)
        self.scalers["device"].fit(
            dev_df.values[dev_df.index.isin(train_df["device_hash"].unique())]
        )
        self.device_features = self.scalers["device"].transform(
            dev_df.values
        ).astype(np.float32)

        # ----------------------------------------------------------------
        # IP nodes
        # ----------------------------------------------------------------
        ip_df = df.groupby("ip_address").agg(
            ip_seen_count          = ("ip_seen_count",  "first"),
            is_ip_shared           = ("is_ip_shared",   "first"),
            txn_count              = ("transaction_id", "count"),
            unique_accounts_per_ip = ("sender_account", "nunique"),
        ).fillna(0)
        ip_df["ip_shared_risk_score"] = (
            ip_df["unique_accounts_per_ip"] / np.log1p(ip_df["txn_count"])
        )
        ip_df = ip_df.reindex(self.ip_mapping, fill_value=0)
        self.scalers["ip"].fit(
            ip_df.values[ip_df.index.isin(train_df["ip_address"].unique())]
        )
        ip_base = self.scalers["ip"].transform(ip_df.values).astype(np.float32)

        if self.USE_IP_GEO_REGION and "ip_geo_region" in df.columns:
            self.ip_geo_encoder.fit(
                train_df.groupby("ip_address")["ip_geo_region"]
                .first().reindex(
                    train_df["ip_address"].unique(), fill_value="Unknown"
                ).values.reshape(-1, 1)
            )
            ip_geo_all = self.ip_geo_encoder.transform(
                df.groupby("ip_address")["ip_geo_region"]
                .first().reindex(self.ip_mapping, fill_value="Unknown")
                .values.reshape(-1, 1)
            ).astype(np.float32)
            self.ip_features = np.hstack([ip_base, ip_geo_all]).astype(np.float32)
        else:
            self.ip_features = ip_base

        # ----------------------------------------------------------------
        # Merchant nodes
        # ----------------------------------------------------------------
        merch_df = df.groupby("merchant_category").agg(
            merchant_fraud_rate        = ("merchant_fraud_rate_computed", "first"),
            merchant_transaction_count = ("transaction_id",               "count"),
            merchant_total_volume      = ("amount_ngn",                   "sum"),
        ).fillna(0)
        merch_df = merch_df.reindex(self.merchant_mapping, fill_value=0)
        self.scalers["merchant"].fit(
            merch_df.values[merch_df.index.isin(train_df["merchant_category"].unique())]
        )
        self.merchant_features = self.scalers["merchant"].transform(
            merch_df.values
        ).astype(np.float32)

        self.transaction_labels = df["is_fraud"].values.astype(np.int64)

        print(
            f"   Shapes — Acc: {self.account_features.shape}  "
            f"Txn: {self.transaction_features.shape}  "
            f"  (tabular_dim={self.tabular_dim}, narrative_dim={self.raw_narrative_dim})\n"
            f"             Dev: {self.device_features.shape}  "
            f"IP: {self.ip_features.shape}  "
            f"Merch: {self.merchant_features.shape}"
        )

    # ------------------------------------------------------------------
    # 3a. Narrative sub-pipeline
    # ------------------------------------------------------------------

    def _build_narrative_features(
        self,
        df: pd.DataFrame,
        train_idx: np.ndarray,
    ) -> np.ndarray:
        n = len(df)

        if self.memo_col is None or self.memo_col not in df.columns:
            print("   NarrativeEncoder: no memo column — using zero embeddings")
            self.raw_narrative_dim    = 0
            self.narrative_embeddings = np.zeros((n, 0), dtype=np.float32)
            return self.narrative_embeddings

        texts = df[self.memo_col].fillna("").tolist()

        self.narrative_encoder = NarrativeEncoder()
        train_embs = self.narrative_encoder.fit_transform([texts[i] for i in train_idx])
        emb_dim    = train_embs.shape[1]

        all_embs = np.zeros((n, emb_dim), dtype=np.float32)
        all_embs[train_idx] = train_embs

        non_train = np.setdiff1d(np.arange(n), train_idx)
        if len(non_train):
            all_embs[non_train] = self.narrative_encoder.transform(
                [texts[i] for i in non_train]
            )

        self.raw_narrative_dim    = emb_dim
        self.narrative_embeddings = all_embs
        print(f"   NarrativeEncoder: raw_dim={emb_dim} (projection inside GNN)")
        return all_embs.astype(np.float32)

    # ------------------------------------------------------------------
    # 4. Edge weights
    # ------------------------------------------------------------------

    def compute_edge_weights_normalized(self) -> None:
        df = self.df

        def _snorm(arr: np.ndarray) -> np.ndarray:
            mu, sd = arr.mean(), arr.std()
            return expit((arr - mu) / (sd + 1e-8)).astype(np.float32)

        ratio = df["amount_ngn"].values / (df["user_avg_txn_amt"].values + 1)
        self.txn_sender_weight = _snorm(ratio)

        rec_avg = df.groupby("receiver_account")["amount_ngn"].transform("mean").fillna(0).values
        self.txn_receiver_weight = _snorm(df["amount_ngn"].values / (rec_avg + 1))

        dev_cnt = df.groupby("device_hash")["transaction_id"].transform("count").values
        self.txn_device_weight = _snorm(1.0 / np.log1p(dev_cnt))

        ip_cnt = df.groupby("ip_address")["transaction_id"].transform("count").values
        self.txn_ip_weight = _snorm(1.0 / np.log1p(ip_cnt))

        self.txn_merchant_weight = expit(
            df["merchant_fraud_rate_computed"].values
        ).astype(np.float32)

    # ------------------------------------------------------------------
    # 5. Edge construction
    # ------------------------------------------------------------------

    def create_edges_interval_based(self, train_idx: Optional[np.ndarray] = None) -> None:
        """
        Build all graph edges.

        Parameters
        ----------
        train_idx : np.ndarray, optional
            Indices of training rows.  MUST be provided to avoid data leakage
            in money_flow and connected_to edges.  If None, falls back to
            using the full dataset with a leakage warning.
        """
        # [IMP-1] Warn if train_idx not provided
        if train_idx is None:
            print(
                "   ⚠  create_edges_interval_based() called without train_idx.\n"
                "      money_flow and connected_to edges will use the FULL dataset.\n"
                "      Pass train_idx to eliminate future-data leakage."
            )
            train_df = self.df
        else:
            train_df = self.df.iloc[train_idx]

        self.compute_edge_weights_normalized()

        self.txn_sender_edges   = np.vstack([self.transaction_ids, self.sender_account_idx])
        self.txn_receiver_edges = np.vstack([self.transaction_ids, self.receiver_account_idx])
        self.txn_device_edges   = np.vstack([self.transaction_ids, self.device_ids])
        self.txn_ip_edges       = np.vstack([self.transaction_ids, self.ip_ids])
        self.txn_merchant_edges = np.vstack([self.transaction_ids, self.merchant_ids])

        self.device_account_edges, self.device_account_weights = self._bipartite_edges(
            self.df, "device_hash", "sender_account",
            self.device_to_idx, self.account_to_idx,
        )
        self.ip_account_edges, self.ip_account_weights = self._bipartite_edges(
            self.df, "ip_address", "sender_account",
            self.ip_to_idx, self.account_to_idx,
        )
        self.account_merchant_edges, self.account_merchant_weights = (
            self._account_merchant_edges()
        )
        self._build_temporal_edges()

        # [FIX-L2] Pass train_idx so connected_to edges use training history only
        self._build_account_account_edges(train_idx=train_idx)

        # [FIX-L1] Pass train_df so money_flow edges use training history only
        self._build_money_flow_edges(train_df=train_df)

        print(
            f"   Edges — "
            f"Txn→Sender: {self.txn_sender_edges.shape[1]:,}  "
            f"Txn→Receiver: {self.txn_receiver_edges.shape[1]:,}  "
            f"Dev→Acc: {self.device_account_edges.shape[1]:,}  "
            f"IP→Acc: {self.ip_account_edges.shape[1]:,}  "
            f"Acc→Merch: {self.account_merchant_edges.shape[1]:,}  "
            f"Txn-Temp: {self.txn_temporal_edges.shape[1]:,}  "
            f"Acc-Acc(device): {self.account_account_edges.shape[1]:,}  "
            f"Acc-Acc(flow): {self.money_flow_edges.shape[1]:,}"
        )

    @staticmethod
    def _bipartite_edges(
        df: pd.DataFrame,
        src_col: str, dst_col: str,
        src_map: dict, dst_map: dict,
    ) -> tuple[np.ndarray, np.ndarray]:
        grp = df.groupby([src_col, dst_col]).size().reset_index(name="_n")
        si  = grp[src_col].map(src_map).values
        di  = grp[dst_col].map(dst_map).values
        ok  = pd.notna(si) & pd.notna(di)
        si, di = si[ok].astype(int), di[ok].astype(int)
        if len(si) == 0:
            return np.empty((2, 0), np.int64), np.empty(0, np.float32)
        return np.vstack([si, di]).astype(np.int64), np.ones(len(si), np.float32)

    def _account_merchant_edges(self) -> tuple[np.ndarray, np.ndarray]:
        am = self.df.groupby(["sender_account", "merchant_category"]).agg(
            txn_count=("transaction_id", "count")
        ).reset_index()
        ai = am["sender_account"].map(self.account_to_idx).values
        mi = am["merchant_category"].map(self.merchant_to_idx).values
        ok = pd.notna(ai) & pd.notna(mi)
        ai, mi = ai[ok].astype(int), mi[ok].astype(int)
        cnt = am.loc[ok, "txn_count"].values
        wts = expit(np.log1p(cnt) / 10.0).astype(np.float32)
        if len(ai) == 0:
            return np.empty((2, 0), np.int64), np.empty(0, np.float32)
        return np.vstack([ai, mi]).astype(np.int64), wts

    def _build_temporal_edges(self) -> None:
        ds = self.df.sort_values(["sender_account", "timestamp"]).reset_index(drop=True)
        ds["node_idx"]      = ds["transaction_id"].map(self.txn_id_to_node_idx)
        ds["next_node_idx"] = ds.groupby("sender_account")["node_idx"].shift(-1)
        ds["next_ts"]       = ds.groupby("sender_account")["timestamp"].shift(-1)
        ds["gap_h"] = (
            pd.to_datetime(ds["next_ts"]) - pd.to_datetime(ds["timestamp"])
        ).dt.total_seconds() / 3600.0

        mask = ds["gap_h"].between(0, 1, inclusive="both") & ds["next_node_idx"].notna()
        if mask.any():
            src = ds.loc[mask, "node_idx"].astype(int).values
            dst = ds.loc[mask, "next_node_idx"].astype(int).values
            self.txn_temporal_edges   = np.vstack([src, dst]).astype(np.int64)
            self.txn_temporal_weights = expit(
                1.0 / (ds.loc[mask, "gap_h"].values + 0.1)
            ).astype(np.float32)
        else:
            self.txn_temporal_edges   = np.empty((2, 0), np.int64)
            self.txn_temporal_weights = np.empty(0, np.float32)

    def _build_account_account_edges(
        self,
        train_idx: Optional[np.ndarray] = None,   # [FIX-L2]
        max_txns_per_device: int = 500,
        min_shared_events:   int = 2,
    ) -> None:
        """
        Build account–account (connected_to) edges via shared device within 24 h.

        [FIX-L2] Now uses train_idx rows only, so future device-sharing
        patterns don't bleed into the training graph.

        Guard rails
        -----------
        max_txns_per_device : skip high-traffic devices (POS terminals etc.)
                              to avoid O(N²) pair explosion. Default 500.
        min_shared_events   : require ≥2 co-occurrences before linking.
                              Filters one-off coincidental co-uses.
        """
        print("   Building account-account edges (train-only, guarded)…")

        # [FIX-L2] Restrict to training rows
        if train_idx is not None:
            tmp = self.df.iloc[train_idx][
                ["sender_account", "device_hash", "timestamp"]
            ].copy()
            # Re-map indices from the subset
            tmp["acc_idx"] = tmp["sender_account"].map(self.account_to_idx)
            tmp["dev_idx"] = tmp["device_hash"].map(self.device_to_idx)
        else:
            tmp = self.df[["sender_account", "device_hash", "timestamp"]].copy()
            tmp["acc_idx"] = self.sender_account_idx
            tmp["dev_idx"] = self.device_ids

        tmp = tmp.dropna(subset=["acc_idx", "dev_idx"])
        tmp["acc_idx"] = tmp["acc_idx"].astype(int)
        tmp["dev_idx"] = tmp["dev_idx"].astype(int)
        tmp = tmp.sort_values(["dev_idx", "timestamp"]).reset_index(drop=True)

        dev_sizes   = tmp.groupby("dev_idx").size()
        hot_devices = set(dev_sizes[dev_sizes > max_txns_per_device].index)
        if hot_devices:
            print(
                f"   ⚠  Skipping {len(hot_devices):,} high-traffic devices "
                f"(> {max_txns_per_device} txns) to avoid O(N²) expansion."
            )

        pair_counts: dict[tuple[int, int], int] = {}
        window = np.timedelta64(24, "h")

        for dev_idx, grp in tmp.groupby("dev_idx"):
            if dev_idx in hot_devices:
                continue
            if len(grp) < 2:
                continue
            ts   = grp["timestamp"].values
            accs = grp["acc_idx"].values
            for i in range(len(ts)):
                end  = int(np.searchsorted(ts, ts[i] + window, side="right"))
                uniq = set(accs[i:end])
                if len(uniq) < 2:
                    continue
                for u, v in itertools.combinations(uniq, 2):
                    if u != v:
                        key = (min(u, v), max(u, v))
                        pair_counts[key] = pair_counts.get(key, 0) + 1

        qualified = [
            pair for pair, cnt in pair_counts.items()
            if cnt >= min_shared_events
        ]

        if qualified:
            edges = np.array(sorted(qualified), np.int64).T
            self.account_account_edges   = edges
            self.account_account_weights = np.ones(edges.shape[1], np.float32)
        else:
            self.account_account_edges   = np.empty((2, 0), np.int64)
            self.account_account_weights = np.empty(0, np.float32)

        print(
            f"   account-account edges: {self.account_account_edges.shape[1]:,}  "
            f"(train-only={train_idx is not None}, "
            f"min_shared_events={min_shared_events}, "
            f"max_txns_per_device={max_txns_per_device})"
        )

    def _build_money_flow_edges(
        self,
        train_df: Optional[pd.DataFrame] = None,   # [FIX-L1]
    ) -> None:
        """
        Direct sender_account → receiver_account edges weighted by total
        transferred amount.

        [FIX-L1] Now accepts train_df and builds edges from TRAINING DATA
        ONLY.  Previously the full dataset was used, leaking future transfer
        volumes (val/test periods) into training-time edge weights.

        Edge weight = sigmoid( log1p(total_amount) / scale )
        where scale is chosen so that 1× the median flow gives weight ~0.5.
        """
        source_df = train_df if train_df is not None else self.df
        if train_df is None:
            print("   ⚠  _build_money_flow_edges: using full dataset (train_df not supplied).")

        flow = source_df.groupby(
            ["sender_account", "receiver_account"], sort=False
        )["amount_ngn"].sum().reset_index(name="total_amount")

        flow = flow[flow["sender_account"] != flow["receiver_account"]]

        si = flow["sender_account"].map(self.account_to_idx).values
        di = flow["receiver_account"].map(self.account_to_idx).values
        ok = pd.notna(si) & pd.notna(di)
        si = si[ok].astype(np.int64)
        di = di[ok].astype(np.int64)
        amounts = flow.loc[ok, "total_amount"].values

        if len(si) == 0:
            self.money_flow_edges   = np.empty((2, 0), np.int64)
            self.money_flow_weights = np.empty(0, np.float32)
            return

        log_amounts = np.log1p(amounts)
        scale = np.median(log_amounts) if np.median(log_amounts) > 0 else 1.0
        weights = expit(log_amounts / scale).astype(np.float32)

        self.money_flow_edges   = np.vstack([si, di]).astype(np.int64)
        self.money_flow_weights = weights
        print(
            f"   money-flow edges (Acc→Acc, train-only={train_df is not None}): "
            f"{self.money_flow_edges.shape[1]:,}"
        )

    def time_based_train_val_test_split(
        self,
        train_ratio: float = 0.70,
        val_ratio:   float = 0.15,
        test_ratio:  float = 0.15,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        assert abs(train_ratio + val_ratio + test_ratio - 1.0) < 1e-6
        ts    = pd.to_datetime(self.df["timestamp"])
        st    = ts.sort_values()
        t_cut = st.quantile(train_ratio)
        v_cut = st.quantile(train_ratio + val_ratio)

        train_idx = np.where(ts <= t_cut)[0]
        val_idx   = np.where((ts > t_cut) & (ts <= v_cut))[0]
        test_idx  = np.where(ts > v_cut)[0]
        n         = len(ts)
        print(
            f"   Split — Train: {len(train_idx):,} ({len(train_idx)/n:.1%})  "
            f"Val: {len(val_idx):,} ({len(val_idx)/n:.1%})  "
            f"Test: {len(test_idx):,} ({len(test_idx)/n:.1%})"
        )
        return train_idx, val_idx, test_idx

    # ------------------------------------------------------------------
    # 7. Build HeteroData
    # ------------------------------------------------------------------

    def build_hetero_data(self) -> HeteroData:
        assert self.account_features.shape[0]     == self.num_accounts
        assert self.transaction_features.shape[0] == self.num_transactions
        assert self.device_features.shape[0]      == self.num_devices
        assert self.ip_features.shape[0]          == self.num_ips
        assert self.merchant_features.shape[0]    == self.num_merchants

        data = HeteroData()
        data["account"].x     = torch.from_numpy(self.account_features)
        data["transaction"].x = torch.from_numpy(self.transaction_features)
        data["device"].x      = torch.from_numpy(self.device_features)
        data["ip"].x          = torch.from_numpy(self.ip_features)
        data["merchant"].x    = torch.from_numpy(self.merchant_features)
        data["transaction"].y = torch.from_numpy(self.transaction_labels)

        def _e(rel, idx, wt):
            data[rel].edge_index  = torch.tensor(idx, dtype=torch.long)
            data[rel].edge_weight = torch.tensor(wt,  dtype=torch.float)

        _e(("transaction", "sent_by",       "account"),  self.txn_sender_edges,   self.txn_sender_weight)
        _e(("transaction", "sent_to",       "account"),  self.txn_receiver_edges, self.txn_receiver_weight)
        _e(("transaction", "used_device",   "device"),   self.txn_device_edges,   self.txn_device_weight)
        _e(("transaction", "from_ip",       "ip"),       self.txn_ip_edges,       self.txn_ip_weight)
        _e(("transaction", "from_merchant", "merchant"), self.txn_merchant_edges, self.txn_merchant_weight)

        if self.device_account_edges.size > 0:
            _e(("device", "used_by", "account"), self.device_account_edges, self.device_account_weights)
        if self.ip_account_edges.size > 0:
            _e(("ip", "used_by", "account"), self.ip_account_edges, self.ip_account_weights)
        if self.account_merchant_edges.size > 0:
            _e(("account", "transacts_with", "merchant"), self.account_merchant_edges, self.account_merchant_weights)
        if self.account_account_edges.size > 0:
            _e(("account", "connected_to", "account"), self.account_account_edges, self.account_account_weights)
        if self.money_flow_edges.size > 0:
            _e(("account", "money_flow", "account"), self.money_flow_edges, self.money_flow_weights)
        if self.txn_temporal_edges.size > 0:
            _e(("transaction", "followed_by", "transaction"), self.txn_temporal_edges, self.txn_temporal_weights)

        data = ToUndirected()(data)
        return data


# ---------------------------------------------------------------------------
# GNN model
# ---------------------------------------------------------------------------

class AMLHeteroGNN(nn.Module):
    """
    Heterogeneous GNN for AML fraud detection.
    Input dimensions are inferred from builder attributes.
    """

    def __init__(
        self,
        tabular_dim:        int,
        raw_narrative_dim:  int,
        account_dim:        int,
        device_dim:         int,
        ip_dim:             int,
        merchant_dim:       int,
        narrative_proj_dim: int   = 32,
        hidden_dim:         int   = 128,
        num_classes:        int   = 2,
        dropout:            float = 0.3,
    ):
        super().__init__()

        self.tabular_dim       = tabular_dim
        self.raw_narrative_dim = raw_narrative_dim

        if raw_narrative_dim > 0:
            self.narrative_projection = NarrativeProjection(
                emb_dim=raw_narrative_dim, out_dim=narrative_proj_dim
            )
            txn_in = tabular_dim + narrative_proj_dim
        else:
            self.narrative_projection = None
            txn_in = tabular_dim

        self.enc = nn.ModuleDict({
            "transaction": nn.Linear(txn_in,       hidden_dim),
            "account":     nn.Linear(account_dim,  hidden_dim),
            "device":      nn.Linear(device_dim,   hidden_dim),
            "ip":          nn.Linear(ip_dim,        hidden_dim),
            "merchant":    nn.Linear(merchant_dim, hidden_dim),
        })

        edge_types = [
            ("transaction", "sent_by",            "account"),
            ("transaction", "sent_to",            "account"),
            ("account",     "rev_sent_by",        "transaction"),
            ("account",     "rev_sent_to",        "transaction"),
            ("transaction", "used_device",        "device"),
            ("device",      "rev_used_device",    "transaction"),
            ("transaction", "from_ip",            "ip"),
            ("ip",          "rev_from_ip",        "transaction"),
            ("transaction", "from_merchant",      "merchant"),
            ("merchant",    "rev_from_merchant",  "transaction"),
            ("device",      "used_by",            "account"),
            ("account",     "rev_used_by_device", "device"),
            ("ip",          "used_by",            "account"),
            ("account",     "rev_used_by_ip",     "ip"),
            ("account",     "transacts_with",     "merchant"),
            ("merchant",    "rev_transacts_with", "account"),
            ("account",     "connected_to",       "account"),
            ("account",     "money_flow",         "account"),
            ("transaction", "followed_by",        "transaction"),
            ("transaction", "rev_followed_by",    "transaction"),
        ]
        self.conv1 = HeteroConv(
            {et: SAGEConv((-1, -1), hidden_dim) for et in edge_types}, aggr="mean"
        )
        self.conv2 = HeteroConv(
            {et: SAGEConv((-1, -1), hidden_dim) for et in edge_types}, aggr="mean"
        )

        self.classifier = nn.Sequential(
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim // 2, num_classes),
        )

    def forward(
        self,
        data: HeteroData,
    ) -> tuple[torch.Tensor, dict[str, torch.Tensor]]:
        txn_x = data["transaction"].x
        if self.narrative_projection is not None and self.raw_narrative_dim > 0:
            tabular   = txn_x[:, :self.tabular_dim]
            raw_narr  = txn_x[:, self.tabular_dim:]
            proj_narr = self.narrative_projection(raw_narr)
            txn_x     = torch.cat([tabular, proj_narr], dim=1)

        x_dict = {
            "transaction": self.enc["transaction"](txn_x),
            "account":     self.enc["account"](data["account"].x),
            "device":      self.enc["device"](data["device"].x),
            "ip":          self.enc["ip"](data["ip"].x),
            "merchant":    self.enc["merchant"](data["merchant"].x),
        }

        x_dict = self.conv1(x_dict, data.edge_index_dict)
        x_dict = {k: torch.relu(v) for k, v in x_dict.items()}
        x_dict = self.conv2(x_dict, data.edge_index_dict)
        x_dict = {k: torch.relu(v) for k, v in x_dict.items()}

        logits = self.classifier(x_dict["transaction"])
        return logits, x_dict


# ---------------------------------------------------------------------------
# Training / evaluation helpers
# ---------------------------------------------------------------------------

def train_one_epoch(
    model: AMLHeteroGNN,
    loader: NeighborLoader,
    optimizer: torch.optim.Optimizer,
    device: torch.device,
    class_weights: Optional[torch.Tensor] = None,
) -> float:
    import torch.nn.functional as F
    model.train()
    total_loss = 0.0
    for batch in loader:
        batch = batch.to(device)
        optimizer.zero_grad()
        logits, _ = model(batch)
        seed_size = batch["transaction"].batch_size
        y         = batch["transaction"].y[:seed_size]
        out       = logits[:seed_size]
        loss = F.cross_entropy(out, y, weight=class_weights)
        loss.backward()
        optimizer.step()
        total_loss += loss.item()
    return total_loss / len(loader)


@torch.no_grad()
def evaluate(
    model: AMLHeteroGNN,
    loader: NeighborLoader,
    device: torch.device,
    class_weights: Optional[torch.Tensor] = None,
) -> dict[str, float]:
    import torch.nn.functional as F
    model.eval()
    all_logits, all_labels = [], []
    total_loss = 0.0

    for batch in loader:
        batch = batch.to(device)
        logits, _ = model(batch)
        seed_size = batch["transaction"].batch_size
        out = logits[:seed_size]
        y   = batch["transaction"].y[:seed_size]
        total_loss += F.cross_entropy(out, y, weight=class_weights).item()
        all_logits.append(out.cpu())
        all_labels.append(y.cpu())

    all_logits = torch.cat(all_logits)
    all_labels = torch.cat(all_labels)
    probs      = torch.softmax(all_logits, dim=1)[:, 1]
    preds      = all_logits.argmax(dim=1)

    tp = ((preds == 1) & (all_labels == 1)).sum().item()
    fp = ((preds == 1) & (all_labels == 0)).sum().item()
    fn = ((preds == 0) & (all_labels == 1)).sum().item()
    precision = tp / (tp + fp + 1e-9)
    recall    = tp / (tp + fn + 1e-9)
    f1        = 2 * precision * recall / (precision + recall + 1e-9)

    # AUROC — [FIX-L3] catches ValueError for single-class validation sets
    try:
        from sklearn.metrics import roc_auc_score, average_precision_score
        labels_np = all_labels.numpy()
        probs_np  = probs.numpy()
        auroc  = float(roc_auc_score(labels_np, probs_np))
        auprc  = float(average_precision_score(labels_np, probs_np))  # [IMP-4]
    except (ImportError, ValueError):
        # ValueError fires when val set contains only one class
        auroc = float("nan")
        auprc = float("nan")

    return {
        "loss":      total_loss / len(loader),
        "accuracy":  (preds == all_labels).float().mean().item(),
        "precision": precision,
        "recall":    recall,
        "f1":        f1,
        "auroc":     auroc,
        "auprc":     auprc,   # [IMP-4] better metric for imbalanced fraud data
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def compute_class_weights(
    df: pd.DataFrame,
    device: torch.device,
) -> torch.Tensor:
    """[IMP-5] Return inverse-frequency class weights for cross-entropy loss."""
    fraud_rate = df["is_fraud"].mean()
    w = torch.tensor([1.0, (1.0 - fraud_rate) / (fraud_rate + 1e-9)], device=device)
    print(f"   Class weights: legit={w[0]:.2f}  fraud={w[1]:.2f}")
    return w


NIGERIAN_DATASET_COLUMN_MAP = {
    "new_device_transaction": "new_device_transaction",
    "merchant_fraud_rate":    "merchant_fraud_rate",
    "persona_fraud_risk":     "persona_fraud_risk",
    "location_fraud_risk":    "location_fraud_risk",
}


def prepare_nigerian_dataset(path: str) -> pd.DataFrame:
    """
    Load and lightly prepare the Nigerian Financial Transactions dataset.
    Drops fraud_type to prevent target leakage.
    """
    print(f"   Loading dataset from: {path}")
    if path.endswith(".parquet"):
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path, low_memory=False)

    for col in ("is_fraud", "bvn_linked", "new_device_transaction",
                "geospatial_velocity_anomaly"):
        if col in df.columns:
            df[col] = df[col].astype(bool).astype(int)

    df["timestamp"]      = pd.to_datetime(df["timestamp"])
    df["transaction_id"] = df["transaction_id"].astype(str)

    if "fraud_type" in df.columns:
        df = df.drop(columns=["fraud_type"])
        print("   Dropped 'fraud_type' column (label leakage risk)")

    print(
        f"   Loaded {len(df):,} transactions  |  "
        f"fraud rate = {df['is_fraud'].mean():.2%}"
    )
    return df


# ---------------------------------------------------------------------------
# PyG version-aware NeighborLoader factory
# ---------------------------------------------------------------------------

def _make_neighbor_loader(
    data: HeteroData,
    per_relation_neighbors: dict,
    batch_size: int,
    input_nodes: tuple,
    shuffle: bool,
) -> NeighborLoader:
    """
    Construct a NeighborLoader with per-relation num_neighbors if PyG ≥ 2.3.0,
    otherwise fall back to a flat list.
    """
    try:
        import torch_geometric
        major, minor, *_ = torch_geometric.__version__.split(".")
        pyg_supports_dict = (int(major), int(minor)) >= (2, 3)
    except Exception:
        pyg_supports_dict = False

    if pyg_supports_dict:
        num_neighbors = per_relation_neighbors
    else:
        fallback = next(iter(per_relation_neighbors.values()))
        print(
            f"   ⚠  PyG < 2.3.0 detected — using flat num_neighbors={fallback}. "
            f"Upgrade PyG for per-relation control."
        )
        num_neighbors = fallback

    return NeighborLoader(
        data,
        num_neighbors=num_neighbors,
        batch_size=batch_size,
        input_nodes=input_nodes,
        shuffle=shuffle,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(
    dataset_path: Optional[str] = None,
    force_recompute_rates: bool = False,
    run_training: bool = False,
    num_epochs: int = 20,
):
    print("=" * 60)
    print("PRODUCTION AML GRAPH BUILDER — v7 (All Leakage Fixes)")
    print("=" * 60)

    if dataset_path is not None:
        df = prepare_nigerian_dataset(dataset_path)
    else:
        print(
            "\n   ⚠  No dataset path provided — using synthetic data for smoke-test.\n"
            "   Pass dataset_path='Nigerian_Fraud_Dataset.csv' to use real data.\n"
        )
        np.random.seed(42)
        n = 50_000
        accounts = [f"U{i}" for i in range(5000)]
        df = pd.DataFrame({
            "transaction_id":              [str(i) for i in range(n)],
            "sender_account":              np.random.choice(accounts, n),
            "receiver_account":            np.random.choice(accounts, n),
            "amount_ngn":                  np.random.exponential(10_000, n),
            "timestamp":                   pd.date_range("2024-01-01", periods=n, freq="5min"),
            "payment_channel":             np.random.choice(["USSD", "Mobile App", "Card", "Bank Transfer"], n),
            "transaction_type":            np.random.choice(["Transfer", "Withdrawal", "Deposit", "POS", "Airtime"], n),
            "merchant_category":           np.random.choice(["Jumia", "MTN Airtime", "Bet9ja", "Other"], n),
            "location":                    np.random.choice(["Lagos", "Abuja", "Kano", "PH"], n),
            "device_hash":                 np.random.choice([f"D{i}" for i in range(2000)], n),
            "ip_address":                  np.random.choice([f"IP{i}" for i in range(1000)], n),
            "is_fraud":                    np.random.choice([0, 1], n, p=[0.95, 0.05]),
            "bvn_linked":                  np.random.choice([0, 1], n, p=[0.3, 0.7]),
            "user_avg_txn_amt":            np.random.exponential(5_000, n),
            "user_std_txn_amt":            np.random.exponential(2_000, n),
            "user_txn_frequency_24h":      np.random.poisson(5, n),
            "user_txn_count_total":        np.random.poisson(50, n),
            "channel_risk_score":          np.random.uniform(0.3, 0.8, n),
            "geospatial_velocity_anomaly": np.random.choice([0, 1], n, p=[0.98, 0.02]),
            "txn_hour":                    np.random.randint(0, 24, n),
            "is_weekend":                  np.random.choice([0, 1], n),
            "is_salary_week":              np.random.choice([0, 1], n),
            "is_night_txn":                np.random.choice([0, 1], n),
            "txn_count_last_1h":           np.random.poisson(2, n),
            "txn_count_last_24h":          np.random.poisson(15, n),
            "total_amount_last_1h":        np.random.exponential(20_000, n),
            "avg_gap_between_txns":        np.random.exponential(60, n),
            "time_since_last":             np.random.exponential(30, n),
            "device_seen_count":           np.random.poisson(10, n),
            "is_device_shared":            np.random.choice([0, 1], n, p=[0.7, 0.3]),
            "ip_seen_count":               np.random.poisson(20, n),
            "is_ip_shared":                np.random.choice([0, 1], n, p=[0.8, 0.2]),
            "new_device_transaction":      np.random.choice([0, 1], n, p=[0.85, 0.15]),
            "sender_persona":              np.random.choice(["Salary Earner", "Student", "Trader"], n),
            "merchant_fraud_rate":         np.random.uniform(0.01, 0.3, n),
            "persona_fraud_risk":          np.random.uniform(0, 1, n),
            "location_fraud_risk":         np.random.uniform(0, 0.5, n),
            "velocity_score":              np.random.randint(0, 10, n).astype(float),
            "spending_deviation_score":    np.random.uniform(-3, 3, n),
            "geo_anomaly_score":           np.random.uniform(0, 1, n),
        })

    print(f"\n   Loaded {len(df):,} transactions  |  fraud rate = {df['is_fraud'].mean():.2%}\n")

    builder = ProductionAMLGraphBuilder(df, memo_col=None, narrative_out_dim=32)

    print("[1] Risk features…")
    builder.compute_risk_features_no_leakage(force_recompute=force_recompute_rates)

    print("[2] Node mappings…")
    builder.create_node_mappings_vectorized()

    print("[3] Time-based split…")
    train_idx, val_idx, test_idx = builder.time_based_train_val_test_split()

    print("[4] Node features (split-aware)…")
    builder.extract_node_features_vectorized(train_idx)

    print("[5] Edges (train-only for money_flow + connected_to)…")
    builder.create_edges_interval_based(train_idx=train_idx)   # [IMP-2] pass train_idx

    print("[6] HeteroData…")
    hetero_data = builder.build_hetero_data()

    print("[7] NeighborLoaders…")
    per_relation_neighbors = {et: [15, 10] for et in hetero_data.edge_types}

    train_loader = _make_neighbor_loader(
        hetero_data,
        per_relation_neighbors=per_relation_neighbors,
        batch_size=1024,
        input_nodes=("transaction", torch.tensor(train_idx)),
        shuffle=True,
    )
    val_loader = _make_neighbor_loader(
        hetero_data,
        per_relation_neighbors=per_relation_neighbors,
        batch_size=1024,
        input_nodes=("transaction", torch.tensor(val_idx)),
        shuffle=False,
    )

    print("[8] Saving graph…")
    torch.save(hetero_data, "aml_hetero_graph_v7.pt")

    model = AMLHeteroGNN(
        tabular_dim        = builder.tabular_dim,
        raw_narrative_dim  = builder.raw_narrative_dim,
        account_dim        = builder.account_features.shape[1],
        device_dim         = builder.device_features.shape[1],
        ip_dim             = builder.ip_features.shape[1],
        merchant_dim       = builder.merchant_features.shape[1],
        narrative_proj_dim = 32,
        hidden_dim         = 128,
    )

    print("\n" + "=" * 60)
    print("✅  Ready for training.")
    print(f"   Total nodes      : {sum(hetero_data.num_nodes.values()):,}")
    print(f"   Total edges      : {sum(hetero_data.num_edges.values()):,}")
    print(f"   transaction.x    : {tuple(hetero_data['transaction'].x.shape)}")
    print(f"     tabular_dim    : {builder.tabular_dim}")
    print(f"     narrative_dim  : {builder.raw_narrative_dim}")
    print(f"   account.x        : {tuple(hetero_data['account'].x.shape)}  (no fraud label — leakage-free)")
    print(f"   device.x         : {tuple(hetero_data['device'].x.shape)}")
    print(f"   ip.x             : {tuple(hetero_data['ip'].x.shape)}")
    print(f"   merchant.x       : {tuple(hetero_data['merchant'].x.shape)}")
    print(f"   money_flow edges : {builder.money_flow_edges.shape[1]:,}  (train-only, sender→receiver)")
    print(f"   connected_to     : {builder.account_account_edges.shape[1]:,}  (train-only, device co-use)")
    print(f"   Train batches    : {len(train_loader):,}")
    print(f"   Val batches      : {len(val_loader):,}")
    n_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"   Model params     : {n_params:,}")
    print("=" * 60)

    # [IMP-3] Training loop — active when run_training=True
    if run_training:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        model  = model.to(device)
        opt    = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-5)
        cw     = compute_class_weights(df, device)   # [IMP-5]

        best_f1      = 0.0
        best_epoch   = 0
        patience     = 5
        no_improve   = 0

        for epoch in range(1, num_epochs + 1):
            tr_loss = train_one_epoch(model, train_loader, opt, device, cw)
            vm      = evaluate(model, val_loader, device, cw)
            print(
                f"  Epoch {epoch:02d}  "
                f"train_loss={tr_loss:.4f}  "
                f"val_loss={vm['loss']:.4f}  "
                f"val_f1={vm['f1']:.4f}  "
                f"val_auroc={vm['auroc']:.4f}  "
                f"val_auprc={vm['auprc']:.4f}"   # [IMP-4]
            )
            if vm["f1"] > best_f1:
                best_f1    = vm["f1"]
                best_epoch = epoch
                no_improve = 0
                torch.save(model.state_dict(), "aml_best_model_v7.pt")
                print(f"     ✅  New best val F1={best_f1:.4f} — checkpoint saved.")
            else:
                no_improve += 1
                if no_improve >= patience:
                    print(f"   Early stopping at epoch {epoch} (best epoch={best_epoch}).")
                    break

        print(f"\n   Best val F1: {best_f1:.4f} at epoch {best_epoch}")
        print("   Best model saved to aml_best_model_v7.pt")

    return hetero_data, model, train_loader, val_loader


if __name__ == "__main__":
    import sys
    # Usage:
    #   python aml_graph_builder_v7.py                                         # synthetic smoke-test
    #   python aml_graph_builder_v7.py Nigerian_Fraud_Dataset.csv              # real data
    #   python aml_graph_builder_v7.py Nigerian_Fraud_Dataset.csv --recompute  # safe leakage-free path
    #   python aml_graph_builder_v7.py Nigerian_Fraud_Dataset.csv --train      # build + train
    path            = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else None
    force_recompute = "--recompute" in sys.argv
    run_training    = "--train" in sys.argv
    main(dataset_path=path, force_recompute_rates=force_recompute, run_training=run_training)
