{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 4,
      "type": "REAL"
    }
  ],
  "dir": [
    "ASCENDING"
  ],
  "offset": 0,
  "limit": 1,
  "id": "4",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 18,
          "name": "$18",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 20,
          "name": "$20",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 21,
          "name": "$21",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 22,
          "name": "$22",
          "type": "VARCHAR"
        },
        {
          "kind": "DIVIDE",
          "operator": "/",
          "type": "REAL",
          "operands": [
            {
              "kind": "CAST",
              "operator": "CAST",
              "type": "REAL",
              "operands": [
                {
                  "kind": "INPUT_REF",
                  "index": 10,
                  "name": "$10",
                  "type": "INTEGER"
                }
              ]
            },
            {
              "kind": "CASE",
              "operator": "CASE",
              "type": "INTEGER",
              "operands": [
                {
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
                      "kind": "LITERAL",
                      "value": 0,
                      "type": "INTEGER",
                      "nullable": false,
                      "precision": 10
                    }
                  ]
                },
                {
                  "kind": "LITERAL",
                  "value": "NULL",
                  "type": "INTEGER",
                  "nullable": true,
                  "precision": 10
                },
                {
                  "kind": "INPUT_REF",
                  "index": 6,
                  "name": "$6",
                  "type": "INTEGER"
                }
              ]
            }
          ]
        }
      ],
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