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
  "id": "6",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "AND",
        "operator": "AND",
        "type": "BOOLEAN",
        "operands": [
          {
            "kind": "NOT_EQUALS",
            "operator": "<>",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 23,
                "name": "$23",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "OWNER",
                "type": "VARCHAR",
                "nullable": false,
                "precision": -1
              }
            ]
          },
          {
            "kind": "GREATER_THAN_OR_EQUAL",
            "operator": ">=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 10,
                "name": "$10",
                "type": "INTEGER"
              },
              {
                "kind": "LITERAL",
                "value": 8000,
                "type": "INTEGER",
                "nullable": false,
                "precision": 10
              }
            ]
          },
          {
            "kind": "LESS_THAN_OR_EQUAL",
            "operator": "<=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 10,
                "name": "$10",
                "type": "INTEGER"
              },
              {
                "kind": "LITERAL",
                "value": 9000,
                "type": "INTEGER",
                "nullable": false,
                "precision": 10
              }
            ]
          }
        ]
      },
      "variableset": "[]",
      "id": "5",
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
                "index": 16,
                "name": "$16",
                "type": "INTEGER"
              },
              {
                "kind": "INPUT_REF",
                "index": 22,
                "name": "$22",
                "type": "INTEGER"
              }
            ]
          },
          "id": "4",
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
                    "index": 0,
                    "name": "$0",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 17,
                    "name": "$17",
                    "type": "INTEGER"
                  }
                ]
              },
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "district",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "account",
                  "id": "1",
                  "inputs": []
                }
              ]
            },
            {
              "relOp": "LogicalTableScan",
              "table": "disp",
              "id": "3",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}