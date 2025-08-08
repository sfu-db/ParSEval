{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 0,
      "type": "INTEGER"
    }
  ],
  "dir": [
    "ASCENDING"
  ],
  "offset": 0,
  "limit": 1,
  "id": "6",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 14,
          "name": "$14",
          "type": "INTEGER"
        },
        {
          "kind": "INPUT_REF",
          "index": 4,
          "name": "$4",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 5,
          "name": "$5",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 19,
          "name": "$19",
          "type": "VARCHAR"
        }
      ],
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
                "index": 9,
                "name": "$9",
                "type": "INTEGER"
              },
              {
                "kind": "INPUT_REF",
                "index": 15,
                "name": "$15",
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
                    "index": 10,
                    "name": "$10",
                    "type": "INTEGER"
                  }
                ]
              },
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "drivers",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "lapTimes",
                  "id": "1",
                  "inputs": []
                }
              ]
            },
            {
              "relOp": "LogicalTableScan",
              "table": "races",
              "id": "3",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}