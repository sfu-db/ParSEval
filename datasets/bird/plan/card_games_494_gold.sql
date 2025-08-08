{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 16,
      "name": "$16",
      "type": "VARCHAR"
    }
  ],
  "id": "2",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "EQUALS",
        "operator": "=",
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
            "value": "Commander 2014 Oversized",
            "type": "VARCHAR",
            "nullable": false,
            "precision": -1
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