{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 2,
      "type": "INTEGER"
    }
  ],
  "dir": [
    "ASCENDING"
  ],
  "offset": 0,
  "limit": 1,
  "id": "5",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 8,
          "name": "$8",
          "type": "INTEGER"
        },
        {
          "kind": "INPUT_REF",
          "index": 15,
          "name": "$15",
          "type": "VARCHAR"
        },
        {
          "kind": "PLUS",
          "operator": "+",
          "type": "INTEGER",
          "operands": [
            {
              "kind": "PLUS",
              "operator": "+",
              "type": "INTEGER",
              "operands": [
                {
                  "kind": "INPUT_REF",
                  "index": 8,
                  "name": "$8",
                  "type": "INTEGER"
                },
                {
                  "kind": "INPUT_REF",
                  "index": 7,
                  "name": "$7",
                  "type": "INTEGER"
                }
              ]
            },
            {
              "kind": "INPUT_REF",
              "index": 9,
              "name": "$9",
              "type": "INTEGER"
            }
          ]
        }
      ],
      "id": "4",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
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
                    "index": 8,
                    "name": "$8",
                    "type": "INTEGER"
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
                    "index": 0,
                    "name": "$0",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 11,
                    "name": "$11",
                    "type": "VARCHAR"
                  }
                ]
              },
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "satscores",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "schools",
                  "id": "1",
                  "inputs": []
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}