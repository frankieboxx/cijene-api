-- Product images table to store retailer product thumbnails (200x200)
-- Only stores images for products that exist in Atrium ERP purchases.

CREATE TABLE IF NOT EXISTS product_images (
    id SERIAL PRIMARY KEY,
    chain_product_id INTEGER NOT NULL REFERENCES chain_products (id),
    ean VARCHAR(50),
    image_data BYTEA NOT NULL,
    image_format VARCHAR(10) NOT NULL DEFAULT 'jpeg',
    width INTEGER NOT NULL DEFAULT 200,
    height INTEGER NOT NULL DEFAULT 200,
    source_url TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (chain_product_id)
);

-- Index for EAN lookups (cross-chain image search by barcode)
CREATE INDEX IF NOT EXISTS idx_product_images_ean ON product_images (ean);

-- Index for quick existence checks during crawl
CREATE INDEX IF NOT EXISTS idx_product_images_chain_product_id ON product_images (chain_product_id);
