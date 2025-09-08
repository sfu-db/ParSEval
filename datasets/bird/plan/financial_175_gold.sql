{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "OTHER_FUNCTION",
      "operator": "STRFTIME",
      "type": "VARCHAR",
      "operands": [
        {
          "kind": "LITERAL",
          "value": "%Y",
          "type": "CHAR",
          "nullable": false,
          "precision": 2
        },
        {
          "kind": "CAST",
          "operator": "CAST",
          "type": "VARCHAR",
          "operands": [
            {
              "kind": "CAST",
              "operator": "CAST",
              "type": "TIMESTAMP",
              "operands": [
                {
                  "kind": "INPUT_REF",
                  "index": 2,
                  "name": "$2",
                  "type": "DATE"
                }
              ]
            }
          ]
        }
      ]
    }
  ],
  "id": "6",
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
            "index": 8,
            "name": "$8",
            "type": "INTEGER"
          },
          {
            "kind": "LITERAL",
            "value": 130,
            "type": "INTEGER",
            "nullable": false,
            "precision": 10
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
                "index": 6,
                "name": "$6",
                "type": "INTEGER"
              },
              {
                "kind": "INPUT_REF",
                "index": 8,
                "name": "$8",
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
                    "index": 5,
                    "name": "$5",
                    "type": "INTEGER"
                  }
                ]
              },
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "client",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "disp",
                  "id": "1",
                  "inputs": []
                }
              ]
            },
            {
              "relOp": "LogicalTableScan",
              "table": "account",
              "id": "3",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}