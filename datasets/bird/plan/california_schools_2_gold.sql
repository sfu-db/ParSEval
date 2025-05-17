{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 0,
      "type": "FLOAT"
    }
  ],
  "dir": [
    "ASCENDING"
  ],
  "offset": 0,
  "limit": 3,
  "id": "3",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "DIVIDE",
          "operator": "/",
          "type": "FLOAT",
          "operands": [
            {
              "kind": "INPUT_REF",
              "index": 24,
              "name": "$24",
              "type": "FLOAT"
            },
            {
              "kind": "CASE",
              "operator": "CASE",
              "type": "FLOAT",
              "operands": [
                {
                  "kind": "EQUALS",
                  "operator": "=",
                  "type": "BOOLEAN",
                  "operands": [
                    {
                      "kind": "INPUT_REF",
                      "index": 23,
                      "name": "$23",
                      "type": "FLOAT"
                    },
                    {
                      "kind": "LITERAL",
                      "value": 0.0,
                      "type": "FLOAT",
                      "nullable": false,
                      "precision": 15
                    }
                  ]
                },
                {
                  "kind": "LITERAL",
                  "value": "NULL",
                  "type": "FLOAT",
                  "nullable": true,
                  "precision": 15
                },
                {
                  "kind": "INPUT_REF",
                  "index": 23,
                  "name": "$23",
                  "type": "FLOAT"
                }
              ]
            }
          ]
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
                    "index": 10,
                    "name": "$10",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "LITERAL",
                    "value": "Continuation School",
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
                        "kind": "DIVIDE",
                        "operator": "/",
                        "type": "FLOAT",
                        "operands": [
                          {
                            "kind": "INPUT_REF",
                            "index": 24,
                            "name": "$24",
                            "type": "FLOAT"
                          },
                          {
                            "kind": "CASE",
                            "operator": "CASE",
                            "type": "FLOAT",
                            "operands": [
                              {
                                "kind": "EQUALS",
                                "operator": "=",
                                "type": "BOOLEAN",
                                "operands": [
                                  {
                                    "kind": "INPUT_REF",
                                    "index": 23,
                                    "name": "$23",
                                    "type": "FLOAT"
                                  },
                                  {
                                    "kind": "LITERAL",
                                    "value": 0.0,
                                    "type": "FLOAT",
                                    "nullable": false,
                                    "precision": 15
                                  }
                                ]
                              },
                              {
                                "kind": "LITERAL",
                                "value": "NULL",
                                "type": "FLOAT",
                                "nullable": true,
                                "precision": 15
                              },
                              {
                                "kind": "INPUT_REF",
                                "index": 23,
                                "name": "$23",
                                "type": "FLOAT"
                              }
                            ]
                          }
                        ]
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
              "table": "frpm",
              "id": "0",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}