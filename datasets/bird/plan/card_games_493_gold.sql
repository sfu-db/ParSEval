{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 20,
      "name": "$20",
      "type": "VARCHAR"
    }
  ],
  "id": "2",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "LIKE",
        "operator": "LIKE",
        "type": "BOOLEAN",
        "operands": [
          {
            "kind": "INPUT_REF",
            "index": 15,
            "name": "$15",
            "type": "VARCHAR"
          },
          {
            "kind": "LITERAL",
            "value": "%FROM the Vault: Lore%",
            "type": "CHAR",
            "nullable": false,
            "precision": 22
          }
        ]
      },
      "variableset": "[]",
      "id": "1",
      "inputs": [
        {
          "relOp": "LogicalTableScan",
          "table": "sets",
          "id": "0",
          "inputs": []
        }
      ]
    }
  ]
}