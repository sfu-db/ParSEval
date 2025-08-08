{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 23,
      "name": "$23",
      "type": "VARCHAR"
    }
  ],
  "id": "4",
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
            "index": 20,
            "name": "$20",
            "type": "VARCHAR"
          },
          {
            "kind": "LITERAL",
            "value": "commander",
            "type": "VARCHAR",
            "nullable": false,
            "precision": -1
          }
        ]
      },
      "variableset": "[]",
      "id": "3",
      "inputs": [
        {
          "relOp": "LogicalJoin",
          "joinType": "inner",
          "condition": {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 23,
                "name": "$23",
                "type": "VARCHAR"
              },
              {
                "kind": "INPUT_REF",
                "index": 4,
                "name": "$4",
                "type": "VARCHAR"
              }
            ]
          },
          "id": "2",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "sets",
              "id": "0",
              "inputs": []
            },
            {
              "relOp": "LogicalTableScan",
              "table": "set_translations",
              "id": "1",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}