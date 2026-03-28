"""add performance indexes on hot columns

Revision ID: c4e2f1a8b9d3
Revises: 9b8d1e4f2a10
Create Date: 2026-03-09 00:00:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "c4e2f1a8b9d3"
down_revision = "9b8d1e4f2a10"
branch_labels = None
depends_on = None


def upgrade():
    # credit_ledger — queried by user and time for billing summaries
    op.create_index("ix_credit_ledger_user_id", "credit_ledger", ["user_id"])
    op.create_index("ix_credit_ledger_created_at", "credit_ledger", ["created_at"])

    # usage_events — queried by user and time for usage reports
    op.create_index("ix_usage_events_user_id", "usage_events", ["user_id"])
    op.create_index("ix_usage_events_created_at", "usage_events", ["created_at"])
    op.create_index("ix_usage_events_feature", "usage_events", ["feature"])

    # api_request_events — queried by user, time, and feature for analytics
    op.create_index("ix_api_request_events_user_id", "api_request_events", ["user_id"])
    op.create_index("ix_api_request_events_created_at", "api_request_events", ["created_at"])
    op.create_index("ix_api_request_events_feature", "api_request_events", ["feature"])

    # image_prompts — queried by user, time
    op.create_index("ix_image_prompts_user_id", "image_prompts", ["user_id"])
    op.create_index("ix_image_prompts_created_at", "image_prompts", ["created_at"])

    # image_generations — queried by prompt_id
    op.create_index("ix_image_generations_prompt_id", "image_generations", ["prompt_id"])

    # products — queried by user
    op.create_index("ix_products_user_id", "products", ["user_id"])
    op.create_index("ix_products_created_at", "products", ["created_at"])

    # product_images — queried by product
    op.create_index("ix_product_images_product_id", "product_images", ["product_id"])

    # avatar_batches — queried by user, time
    op.create_index("ix_avatar_batches_user_id", "avatar_batches", ["user_id"])
    op.create_index("ix_avatar_batches_created_at", "avatar_batches", ["created_at"])

    # avatar_results — queried by batch
    op.create_index("ix_avatar_results_batch_id", "avatar_results", ["batch_id"])

    # creative_inspirations — queried by user, time
    op.create_index("ix_creative_inspirations_user_id", "creative_inspirations", ["user_id"])
    op.create_index("ix_creative_inspirations_created_at", "creative_inspirations", ["created_at"])

    # creative_batches — queried by user, time
    op.create_index("ix_creative_batches_user_id", "creative_batches", ["user_id"])
    op.create_index("ix_creative_batches_created_at", "creative_batches", ["created_at"])

    # creative_results — queried by batch
    op.create_index("ix_creative_results_batch_id", "creative_results", ["batch_id"])

    # api_keys — queried by user+service
    op.create_index("ix_api_keys_user_id_service", "api_keys", ["user_id", "service"])


def downgrade():
    op.drop_index("ix_api_keys_user_id_service", table_name="api_keys")
    op.drop_index("ix_creative_results_batch_id", table_name="creative_results")
    op.drop_index("ix_creative_batches_created_at", table_name="creative_batches")
    op.drop_index("ix_creative_batches_user_id", table_name="creative_batches")
    op.drop_index("ix_creative_inspirations_created_at", table_name="creative_inspirations")
    op.drop_index("ix_creative_inspirations_user_id", table_name="creative_inspirations")
    op.drop_index("ix_avatar_results_batch_id", table_name="avatar_results")
    op.drop_index("ix_avatar_batches_created_at", table_name="avatar_batches")
    op.drop_index("ix_avatar_batches_user_id", table_name="avatar_batches")
    op.drop_index("ix_product_images_product_id", table_name="product_images")
    op.drop_index("ix_products_created_at", table_name="products")
    op.drop_index("ix_products_user_id", table_name="products")
    op.drop_index("ix_image_generations_prompt_id", table_name="image_generations")
    op.drop_index("ix_image_prompts_created_at", table_name="image_prompts")
    op.drop_index("ix_image_prompts_user_id", table_name="image_prompts")
    op.drop_index("ix_api_request_events_feature", table_name="api_request_events")
    op.drop_index("ix_api_request_events_created_at", table_name="api_request_events")
    op.drop_index("ix_api_request_events_user_id", table_name="api_request_events")
    op.drop_index("ix_usage_events_feature", table_name="usage_events")
    op.drop_index("ix_usage_events_created_at", table_name="usage_events")
    op.drop_index("ix_usage_events_user_id", table_name="usage_events")
    op.drop_index("ix_credit_ledger_created_at", table_name="credit_ledger")
    op.drop_index("ix_credit_ledger_user_id", table_name="credit_ledger")
