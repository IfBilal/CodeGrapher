const express = require("express");
const { chargeCard } = require("./billing");

function createOrder(req, res) {
  chargeCard(req.body.amount);
  db.orders.insert(req.body);
  res.send("ok");
}

function cancelOrder(req, res) {
  db.orders.delete(req.params.id);
  res.send("cancelled");
}

const healthCheck = (req, res) => {
  res.send("healthy");
};

router.post("/orders", createOrder);
router.delete("/orders/:id", cancelOrder);
router.get("/health", healthCheck);
