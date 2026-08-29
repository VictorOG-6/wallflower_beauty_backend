import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException

from models import Order, OrderItem, Product, ProductSubVariant, ProductVariant
from services.inventory import (
    commit_inventory_for_order,
    restore_inventory_for_order,
)


class InventoryLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.product = Product(
            id=uuid4(),
            name="Shirt",
            image_url="shirt.jpg",
            price=10_000,
            category="clothing",
            description="Test shirt",
            quantity=8,
            slug=f"shirt-{uuid4().hex}",
        )
        self.variant = ProductVariant(
            id=uuid4(),
            product_id=self.product.id,
            name="Large red",
            image_url="large-red.jpg",
            color="red",
            quantity=8,
        )
        self.sub_variant = ProductSubVariant(
            id=uuid4(),
            product_variant_id=self.variant.id,
            size="L",
            quantity=8,
        )
        self.order = Order(
            id=uuid4(),
            public_id=f"test-{uuid4().hex}",
            user_id=uuid4(),
            total_price=30_000,
            total_products=3,
        )
        self.item = OrderItem(
            id=uuid4(),
            order_id=self.order.id,
            product_id=self.product.id,
            product_variant_id=self.variant.id,
            quantity=3,
            price_at_purchase=10_000,
        )
        self.sub_variant_item = OrderItem(
            id=uuid4(),
            order_id=self.order.id,
            product_id=self.product.id,
            product_variant_id=self.variant.id,
            product_sub_variant_id=self.sub_variant.id,
            quantity=3,
            price_at_purchase=10_000,
        )
        self.session = MagicMock()
        self.inventory = (
            [self.item],
            {self.product.id: self.product},
            {self.variant.id: self.variant},
            {},
        )
        self.sub_variant_inventory = (
            [self.sub_variant_item],
            {self.product.id: self.product},
            {self.variant.id: self.variant},
            {self.sub_variant.id: self.sub_variant},
        )

    @patch("services.inventory._load_inventory")
    def test_commit_and_restore_variant_inventory_once(self, load_inventory):
        load_inventory.return_value = self.inventory

        commit_inventory_for_order(self.session, self.order)
        committed_at = self.order.inventory_committed_at
        commit_inventory_for_order(self.session, self.order)

        self.assertEqual(self.variant.quantity, 5)
        self.assertEqual(self.product.quantity, 5)
        self.assertEqual(self.order.inventory_committed_at, committed_at)
        self.assertEqual(load_inventory.call_count, 1)

        restore_inventory_for_order(self.session, self.order)
        restored_at = self.order.inventory_restored_at
        restore_inventory_for_order(self.session, self.order)

        self.assertEqual(self.variant.quantity, 8)
        self.assertEqual(self.product.quantity, 8)
        self.assertEqual(self.order.inventory_restored_at, restored_at)
        self.assertEqual(load_inventory.call_count, 2)

    @patch("services.inventory._load_inventory")
    def test_commit_rejects_insufficient_variant_inventory(self, load_inventory):
        self.variant.quantity = 2
        self.product.quantity = 2
        load_inventory.return_value = self.inventory

        with self.assertRaises(HTTPException) as raised:
            commit_inventory_for_order(self.session, self.order)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(self.variant.quantity, 2)
        self.assertIsNone(self.order.inventory_committed_at)

    @patch("services.inventory._load_inventory")
    def test_variant_product_requires_variant_on_order_item(self, load_inventory):
        self.item.product_variant_id = None
        load_inventory.return_value = self.inventory

        with self.assertRaises(HTTPException) as raised:
            commit_inventory_for_order(self.session, self.order)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("variant is required", raised.exception.detail)

    @patch("services.inventory._load_inventory")
    def test_commit_and_restore_sub_variant_inventory_once(self, load_inventory):
        load_inventory.return_value = self.sub_variant_inventory

        commit_inventory_for_order(self.session, self.order)

        self.assertEqual(self.sub_variant.quantity, 5)
        self.assertEqual(self.variant.quantity, 5)
        self.assertEqual(self.product.quantity, 5)

        restore_inventory_for_order(self.session, self.order)

        self.assertEqual(self.sub_variant.quantity, 8)
        self.assertEqual(self.variant.quantity, 8)
        self.assertEqual(self.product.quantity, 8)

    @patch("services.inventory._load_inventory")
    def test_commit_rejects_missing_sub_variant_selection(self, load_inventory):
        self.sub_variant_item.product_sub_variant_id = None
        load_inventory.return_value = self.sub_variant_inventory

        with self.assertRaises(HTTPException) as raised:
            commit_inventory_for_order(self.session, self.order)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("size is required", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
