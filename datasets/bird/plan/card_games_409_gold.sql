{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 76,
      "name": "$76",
      "type": "VARCHAR"
    }
  ],
  "id": "4",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "AND",
        "operator": "AND",
        "type": "BOOLEAN",
        "operands": [
          {
            "kind": "OR",
            "operator": "OR",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "IS_NULL",
                "operator": "IS NULL",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 54,
                    "name": "$54",
                    "type": "VARCHAR"
                  }
                ]
              },
              {
                "kind": "LIKE",
                "operator": "LIKE",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 54,
                    "name": "$54",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "LITERAL",
                    "value": "%*%",
                    "type": "CHAR",
                    "nullable": false,
                    "precision": 3
                  }
                ]
              }
            ]
          },
          {
            "kind": "LIKE",
            "operator": "LIKE",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 76,
                "name": "$76",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "%triggered ability%",
                "type": "CHAR",
                "nullable": false,
                "precision": 19
              }
            ]
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
                "index": 71,
                "name": "$71",
                "type": "VARCHAR"
              },
              {
                "kind": "INPUT_REF",
                "index": 77,
                "name": "$77",
                "type": "VARCHAR"
              }
            ]
          },
          "id": "2",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "cards",
              "id": "0",
              "inputs": []
            },
            {
              "relOp": "LogicalTableScan",
              "table": "rulings",
              "id": "1",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}