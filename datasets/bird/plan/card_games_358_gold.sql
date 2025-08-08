{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 56,
      "name": "$56",
      "type": "VARCHAR"
    }
  ],
  "id": "2",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "AND",
        "operator": "AND",
        "type": "BOOLEAN",
        "operands": [
          {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 48,
                "name": "$48",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "Duress",
                "type": "VARCHAR",
                "nullable": false,
                "precision": -1
              }
            ]
          },
          {
            "kind": "NOT",
            "operator": "NOT",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "IS_NULL",
                "operator": "IS NULL",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 56,
                    "name": "$56",
                    "type": "VARCHAR"
                  }
                ]
              }
            ]
          }
        ]
      },
      "variableset": "[]",
      "id": "1",
      "inputs": [
        {
          "relOp": "LogicalTableScan",
          "table": "cards",
          "id": "0",
          "inputs": []
        }
      ]
    }
  ]
}